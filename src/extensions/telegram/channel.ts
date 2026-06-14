import { join } from "node:path";
import { type Bot, GrammyError, HttpError } from "grammy";
import type { Message } from "grammy/types";

import type { Channel, ChannelRuntime, Delivery, Exchange } from "../../channels/types.ts";
import type { InboundMessage } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";
import { unpackCallbackData } from "./buttons.ts";
import { mapButtonTap, mapMediaMessage, mapReaction, mapTextMessage } from "./inbound.ts";
import {
  buildAttachment,
  downloadMedia,
  generateMediaFilename,
  MediaTooLargeError,
  resolveMedia,
} from "./media.ts";
import { Mutex } from "./mutex.ts";
import type { ChannelMessageDirection } from "./schema.ts";
import {
  deliverText,
  editWithMarkdownFallback,
  sendWithMarkdownFallback,
  startTyping,
} from "./sending.ts";
import { StreamRenderer } from "./streaming.ts";

/** Persists message id ↔ session mappings so a reply-to can be force-routed. */
export interface ChannelMessageStore {
  record(messageId: string, sessionId: number, direction: ChannelMessageDirection): void;
  findSessionId(messageId: string): number | null;
}

export interface TelegramChannelOptions {
  chatId: number;
  allowMedia: boolean;
  pushNotifications: boolean;
  mediaDir: string;
  /** Abort the in-flight agent run — wired from sessions.abortExchange. */
  stop: () => Promise<void>;
  /** Record/lookup message↔session mappings for reply-to routing. */
  store: ChannelMessageStore;
  /** Id of the session currently receiving messages, for outbound recording. */
  currentSessionId: () => number | null;
}

export const STOP_COMMAND = "/stop";
export const STOP_ACKNOWLEDGEMENT = "⏹ Stopped.";

export class TelegramChannel implements Channel {
  readonly name = "telegram";

  private readonly bot: Bot;
  private readonly options: TelegramChannelOptions;
  private readonly mutex = new Mutex();
  private runtime: ChannelRuntime | null = null;
  private lastInboundId: number | null = null;
  private lastOutboundId: number | null = null;
  private activeRenderer: StreamRenderer | null = null;
  /**
   * Provisional message shown during preparation (boundary/preprocessor status
   * lines). respond() seeds the StreamRenderer with it so the streamed response
   * edits it in place — or deletes it via finalize when the exchange has no text.
   */
  private leadInMessageId: number | null = null;
  private shutdownMessageId: number | null = null;

  constructor(bot: Bot, options: TelegramChannelOptions) {
    this.bot = bot;
    this.options = options;
  }

  /** Last message received from the user — target for react_to_message. */
  get lastInboundMessageId(): number | null {
    return this.lastInboundId;
  }

  /** Last message this channel sent — target for pin_message. */
  get lastOutboundMessageId(): number | null {
    return this.lastOutboundId;
  }

  async start(runtime: ChannelRuntime): Promise<void> {
    this.runtime = runtime;

    this.bot.on("message", async (ctx) => {
      if (ctx.chat.id !== this.options.chatId) return;

      if (ctx.message.text != null) {
        await this.handleText(ctx.message);
      } else {
        await this.handleMedia(ctx.message);
      }
    });

    this.bot.on("callback_query:data", async (ctx) => {
      await ctx
        .answerCallbackQuery()
        .catch((error) => runtime.log.warn({ err: error }, "callback answer failed"));

      if (ctx.callbackQuery.from.id !== this.options.chatId) {
        runtime.log.debug({ userId: ctx.callbackQuery.from.id }, "dropping unauthorized tap");
        return;
      }

      const unpacked = unpackCallbackData(ctx.callbackQuery.data);
      if (unpacked == null) {
        runtime.log.warn({ data: ctx.callbackQuery.data }, "unrecognized callback data");
        return;
      }

      const messageId = ctx.callbackQuery.message?.message_id ?? null;

      if (unpacked.singleUse && messageId != null) {
        void this.bot.api
          .editMessageReplyMarkup(this.options.chatId, messageId)
          .catch((error) => runtime.log.warn({ err: error }, "keyboard removal failed"));
      }

      runtime.submit(mapButtonTap(unpacked.value, messageId));
    });

    this.bot.on("message_reaction", (ctx) => {
      if (ctx.chat.id !== this.options.chatId) return;
      if (ctx.messageReaction.user?.id !== this.options.chatId) {
        runtime.log.debug(
          { userId: ctx.messageReaction.user?.id },
          "dropping unauthorized reaction",
        );
        return;
      }

      const inbound = mapReaction(ctx.messageReaction);
      if (inbound == null) return;

      runtime.submit(this.routeReply(inbound));
    });

    this.bot.catch(({ error }) => {
      if (error instanceof GrammyError) {
        runtime.log.error(
          { err: error, errorCode: error.error_code, description: error.description },
          "telegram api rejected request",
        );
      } else if (error instanceof HttpError) {
        runtime.log.error({ err: error }, "could not reach telegram");
      } else {
        runtime.log.error({ err: error }, "telegram update handling failed");
      }
    });

    // init() validates the token via getMe before going live; start() resolves
    // only when polling stops, so it runs detached.
    await this.bot.init();

    // Surface the channel-agnostic prefix commands in Telegram's command menu so
    // they're discoverable; the coordinator parses the prefixes (see submit()).
    await this.bot.api
      .setMyCommands([
        { command: "new", description: "Start a new conversation, ignoring the current topic" },
        {
          command: "queue",
          description: "Queue a message for the next turn instead of interrupting",
        },
      ])
      .catch((error) => runtime.log.warn({ err: error }, "setting bot commands failed"));

    void this.bot
      .start({
        allowed_updates: ["message", "callback_query", "message_reaction"],
        onStart: (me) => runtime.log.info({ username: me.username }, "telegram bot polling"),
      })
      .catch((error) => runtime.log.error({ err: error }, "telegram polling crashed"));
  }

  /**
   * When a message replies to a known past message, stamp the owning session as
   * an explicit resume target. An unknown reply target leaves the message
   * untouched so it follows the normal active-session/boundary path.
   */
  private routeReply(message: InboundMessage): InboundMessage {
    const replyToMessageId = message.metadata.replyToMessageId;
    if (typeof replyToMessageId !== "string") return message;

    const sessionId = this.options.store.findSessionId(replyToMessageId);
    if (sessionId == null) return message;

    return { ...message, metadata: { ...message.metadata, resumeSessionId: sessionId } };
  }

  async respond({ message, events }: Exchange): Promise<void> {
    const log = this.log();

    await this.mutex.run(async () => {
      // Reclaim the preparation lead-in (if any) as the streaming message so the
      // response text replaces it in place instead of opening a second message.
      const seedMessageId = this.leadInMessageId;
      this.leadInMessageId = null;

      const stopTyping = startTyping(this.bot.api, this.options.chatId, log);
      const renderer = new StreamRenderer(this.bot.api, this.options.chatId, log, seedMessageId);
      this.activeRenderer = renderer;

      try {
        for await (const event of events) {
          switch (event.kind) {
            case "text":
              await renderer.appendText(event.text);
              break;

            case "tool-start":
              await renderer.appendTool(event.toolName, event.args);
              break;

            case "status":
              await renderer.showTransient(event.text);
              break;

            case "error":
              await this.sendErrorNotice(event.message, event.recoverable, log);
              break;

            case "result":
              if (event.result != null) {
                log.info(
                  {
                    sessionId: event.sessionId,
                    costUsd: event.result.costUsd,
                    tokens: event.result.usage.totalTokens,
                  },
                  "exchange complete",
                );
              }
              break;

            default:
              break;
          }
        }
      } finally {
        this.activeRenderer = null;
        stopTyping();
      }

      const finalized = await renderer.finalize();
      if (finalized != null) this.lastOutboundId = finalized;

      // Routing has settled by now: map both the user's message and the bot's
      // reply to the receiving session so a future reply-to can resolve them.
      const sessionId = this.options.currentSessionId();
      if (sessionId != null) {
        const inboundId = message.metadata.messageId;
        if (typeof inboundId === "number") {
          this.recordMessage(String(inboundId), sessionId, "incoming");
        }

        if (finalized != null) this.recordMessage(String(finalized), sessionId, "outgoing");
      }
    });
  }

  private recordMessage(
    messageId: string,
    sessionId: number,
    direction: ChannelMessageDirection,
  ): void {
    try {
      this.options.store.record(messageId, sessionId, direction);
    } catch (error) {
      this.log().debug({ err: error, messageId }, "recording channel message failed");
    }
  }

  async deliver(delivery: Delivery): Promise<void> {
    await this.mutex.run(async () => {
      const lastId = await deliverText(
        this.bot.api,
        this.options.chatId,
        delivery.text,
        this.options.pushNotifications,
      );

      if (lastId != null) this.lastOutboundId = lastId;
    });
  }

  status(text: string): void {
    const renderer = this.activeRenderer;

    if (renderer != null) {
      void renderer
        .showTransient(text)
        .catch((error) => this.log().debug({ err: error }, "status rendering failed"));
      return;
    }

    // Preparation phase (no streaming renderer yet): keep the typing indicator
    // alive and surface the line on a provisional lead-in message that respond()
    // hands to the StreamRenderer — so the streamed response replaces it (or
    // deletes it when the exchange yields no text).
    this.pingTyping();
    void this.showLeadIn(text);
  }

  /** One-shot typing action to signal liveness on receipt or during preparation. */
  private pingTyping(): void {
    void this.bot.api
      .sendChatAction(this.options.chatId, "typing")
      .catch((error) => this.log().debug({ err: error }, "typing action failed"));
  }

  /**
   * Surface a preparation status line on a single provisional message, created on
   * the first call and edited in place thereafter. Serialized through the send
   * mutex so it orders cleanly behind any in-flight send; respond() reclaims it as
   * the streaming message, so the lead-in never lingers across a normal exchange.
   */
  private async showLeadIn(text: string): Promise<void> {
    const log = this.log();
    const display = `_${text}_`;

    await this.mutex.run(async () => {
      try {
        if (this.leadInMessageId == null) {
          this.leadInMessageId = await sendWithMarkdownFallback(
            this.bot.api,
            this.options.chatId,
            display,
          );
        } else {
          await editWithMarkdownFallback(
            this.bot.api,
            this.options.chatId,
            this.leadInMessageId,
            display,
          );
        }
      } catch (error) {
        log.debug({ err: error }, "lead-in status failed");
      }
    });
  }

  /**
   * Render shutdown-sequence progress on one dedicated italic message, edited
   * in place across calls. Runs under the mutex so it serializes with the final
   * delivery flush, and is awaited by the coordinator so the update lands before
   * the process exits. The last line ("Done") is intentionally left in the chat.
   */
  async shutdownStatus(text: string): Promise<void> {
    const log = this.log();
    const display = `_${text}_`;

    await this.mutex.run(async () => {
      try {
        if (this.shutdownMessageId == null) {
          this.shutdownMessageId = await sendWithMarkdownFallback(
            this.bot.api,
            this.options.chatId,
            display,
          );
        } else {
          await editWithMarkdownFallback(
            this.bot.api,
            this.options.chatId,
            this.shutdownMessageId,
            display,
          );
        }
      } catch (error) {
        log.warn({ err: error }, "shutdown status update failed");
      }
    });
  }

  async stop(): Promise<void> {
    if (this.bot.isRunning()) await this.bot.stop();
  }

  private async handleText(message: Message): Promise<void> {
    if (message.text?.trim() === STOP_COMMAND) {
      this.lastInboundId = message.message_id;
      await this.handleStop();
      return;
    }

    const inbound = mapTextMessage(message);
    if (inbound == null) return;

    this.lastInboundId = message.message_id;
    // Bridge the gap until respond()'s typing loop takes over.
    this.pingTyping();
    this.runtimeOrThrow().submit(this.routeReply(inbound));
  }

  /** Abort the in-flight run instead of submitting — "/stop" never reaches the agent. */
  private async handleStop(): Promise<void> {
    const log = this.log();

    try {
      await this.options.stop();
    } catch (error) {
      log.warn({ err: error }, "exchange abort failed");
    }

    await this.bot.api
      .sendMessage(this.options.chatId, STOP_ACKNOWLEDGEMENT)
      .catch((error) => log.warn({ err: error }, "stop acknowledgement failed"));
  }

  private async handleMedia(message: Message): Promise<void> {
    const log = this.log();

    if (!this.options.allowMedia) {
      log.debug("ignoring media message — allowMedia is disabled");
      return;
    }

    const resolved = resolveMedia(message);
    if (resolved == null) {
      log.debug("ignoring unresolvable media message");
      return;
    }

    this.lastInboundId = message.message_id;
    // Bridge the download latency until respond()'s typing loop takes over.
    this.pingTyping();

    try {
      const destPath = join(this.options.mediaDir, generateMediaFilename(resolved));
      await downloadMedia(this.bot.api, this.bot.token, resolved, destPath);

      this.runtimeOrThrow().submit(
        this.routeReply(mapMediaMessage(message, buildAttachment(resolved, destPath))),
      );
    } catch (error) {
      log.warn({ err: error }, "media download failed");

      const notice =
        error instanceof MediaTooLargeError
          ? error.message
          : "Failed to download the file. Please try again.";

      await this.bot.api
        .sendMessage(this.options.chatId, notice)
        .catch((sendError) => log.warn({ err: sendError }, "media failure notice failed"));
    }
  }

  private async sendErrorNotice(message: string, recoverable: boolean, log: Logger): Promise<void> {
    const notice = recoverable
      ? `⚠️ Error: ${message}`
      : `⚠️ Error: ${message}\n\nThis needs your attention — the next message won't recover on its own.`;

    await this.bot.api
      .sendMessage(this.options.chatId, notice)
      .catch((error) => log.warn({ err: error }, "error notice failed"));
  }

  private runtimeOrThrow(): ChannelRuntime {
    if (this.runtime == null) throw new Error("telegram channel not started");

    return this.runtime;
  }

  private log(): Logger {
    return this.runtimeOrThrow().log;
  }
}

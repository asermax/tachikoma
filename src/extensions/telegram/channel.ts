import { join } from "node:path";
import { type Bot, GrammyError, HttpError } from "grammy";
import type { Message } from "grammy/types";

import type { Channel, ChannelRuntime, Delivery, Exchange } from "../../channels/types.ts";
import type { AgentEvent } from "../../domain/agent-events.ts";
import type { InboundMessage } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";
import { unpackCallbackData } from "./buttons.ts";
import { toTelegramEntities } from "./entities.ts";
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
  editWithFallback,
  forceNotification,
  sendWithFallback,
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
  /** Minimum streamed-response duration (seconds) before a completion push is forced. */
  pushNotificationMinSeconds: number;
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

/**
 * Agent tools that already deliver a notifying message during the turn (a file, a
 * pin, an inline-button message). When one of these runs the user is already
 * pushed, so forcing an extra push on the streamed response would double-notify —
 * the copy-delete is skipped.
 */
const NOTIFYING_TOOLS = new Set(["send_telegram_file", "pin_message", "send_message_with_buttons"]);

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

    // The mutex is held only for the seed handoff and finalize — not for the entire
    // streaming loop. This keeps the lock free during before_agent_start so that
    // preparation-phase status calls (e.g. skill detection) can send their lead-in
    // messages without queueing behind respond(). The first event is agent_start,
    // emitted by pi after all before_agent_start handlers have settled, so the
    // lead-in is guaranteed to have arrived by the time we claim it.
    const startedAt = Date.now();
    let notifyingToolUsed = false;

    // Initialize the renderer on first event (agent_start), under a brief mutex hold.
    // Returns null if no events arrive (e.g. aborted before agent_start).
    const initResult = await this.initStreamingRenderer(events);
    if (initResult == null) return;

    const { renderer, remainingEvents, stopTyping } = initResult;
    this.activeRenderer = renderer;

    try {
      for await (const event of remainingEvents) {
        switch (event.kind) {
          case "text":
            await renderer.appendText(event.text);
            break;

          case "tool-start":
            // Tools that already push (file/pin/buttons) make a completion push
            // redundant — skip the copy-delete so the user isn't double-notified.
            if (NOTIFYING_TOOLS.has(event.toolName)) notifyingToolUsed = true;
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

    const outboundId = await this.finalizeResponse(renderer, startedAt, notifyingToolUsed, log);

    // Routing has settled by now: map both the user's message and the bot's
    // reply to the receiving session so a future reply-to can resolve them.
    const sessionId = this.options.currentSessionId();
    if (sessionId != null) {
      const inboundId = message.metadata.messageId;
      if (typeof inboundId === "number") {
        this.recordMessage(String(inboundId), sessionId, "incoming");
      }

      if (outboundId != null) this.recordMessage(String(outboundId), sessionId, "outgoing");
    }
  }

  /**
   * Consume the first event from the stream and use it to initialize the streaming
   * renderer under a brief mutex hold. Returns null if no events arrive (e.g.
   * aborted before agent_start). The lead-in message (if any) is claimed as the
   * renderer's seed so the streamed response edits it in place.
   */
  private async initStreamingRenderer(events: AsyncIterable<AgentEvent>): Promise<{
    renderer: StreamRenderer;
    remainingEvents: AsyncIterable<AgentEvent>;
    stopTyping: () => void;
  } | null> {
    const iterator = events[Symbol.asyncIterator]();
    const first = await iterator.next();
    if (first.done) return null;

    // Re-wrap the remaining events as a new iterable.
    const remaining: AsyncIterable<AgentEvent> = {
      [Symbol.asyncIterator]: () => iterator,
    };

    const log = this.log();
    const seedMessageId = this.leadInMessageId;
    this.leadInMessageId = null;
    const stopTyping = startTyping(this.bot.api, this.options.chatId, log);
    const renderer = new StreamRenderer(
      this.bot.api,
      this.options.chatId,
      log,
      seedMessageId,
      this.options.pushNotifications,
    );

    // The mutex serializes with any in-flight showLeadIn that may still be
    // delivering the lead-in message to Telegram.
    await this.mutex.run(async () => {
      this.activeRenderer = renderer;
    });

    return { renderer, remainingEvents: remaining, stopTyping };
  }

  private async finalizeResponse(
    renderer: StreamRenderer,
    startedAt: number,
    notifyingToolUsed: boolean,
    log: Logger,
  ): Promise<number | null> {
    return this.mutex.run(async () => {
      const id = await renderer.finalize();

      // Telegram edits never notify, so a streamed response — edited in place as
      // it arrives — never fires a push. Force one on completion by copying the
      // finalized message (a fresh send that notifies) and deleting the streamed
      // original, but only when the turn ran long enough that the user likely
      // stepped away (quick turns skip it to avoid a flicker while they're still
      // watching) and no notifying tool already pushed. For a multi-message
      // response only the final chunk is copied — it carries the push and its id is
      // what reply routing records; a failure leaves the streamed message in place.
      const elapsedSeconds = (Date.now() - startedAt) / 1000;
      const shouldPush =
        this.options.pushNotifications &&
        !notifyingToolUsed &&
        id != null &&
        elapsedSeconds >= this.options.pushNotificationMinSeconds;

      let outboundId = id;
      if (shouldPush && id != null) {
        try {
          outboundId = await forceNotification(this.bot.api, this.options.chatId, id);
        } catch (error) {
          log.debug(
            { err: error },
            "push notification copy-delete failed; delivered without a push",
          );
        }
      }

      if (outboundId != null) this.lastOutboundId = outboundId;

      return outboundId;
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
   * Send `_${text}_` as a dedicated message, or edit the existing one in place —
   * the shared create-then-edit core of the preparation lead-in and shutdown
   * progress. Returns the message id to keep tracking (a new id on first send,
   * the same id on edit).
   */
  private async upsertDedicatedMessage(
    messageId: number | null,
    text: string,
  ): Promise<number | null> {
    const display = `_${text}_`;
    if (messageId == null) {
      // Silent under push notifications: this serves the preparation lead-in (later
      // reclaimed into the streamed response, which forces its own push on
      // completion) and shutdown status (informational, no push wanted). With push
      // notifications off, default notification behavior applies.
      return sendWithFallback(this.bot.api, this.options.chatId, toTelegramEntities(display), {
        silent: this.options.pushNotifications,
      });
    }
    await editWithFallback(
      this.bot.api,
      this.options.chatId,
      messageId,
      toTelegramEntities(display),
    );
    return messageId;
  }

  /**
   * Surface a preparation status line on a single provisional message, created on
   * the first call and edited in place thereafter. Serialized through the send
   * mutex so it orders cleanly behind any in-flight send; respond() reclaims it as
   * the streaming message, so the lead-in never lingers across a normal exchange.
   */
  private async showLeadIn(text: string): Promise<void> {
    await this.mutex.run(async () => {
      try {
        this.leadInMessageId = await this.upsertDedicatedMessage(this.leadInMessageId, text);
      } catch (error) {
        this.log().debug({ err: error }, "lead-in status failed");
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
    await this.mutex.run(async () => {
      try {
        this.shutdownMessageId = await this.upsertDedicatedMessage(this.shutdownMessageId, text);
      } catch (error) {
        this.log().warn({ err: error }, "shutdown status update failed");
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

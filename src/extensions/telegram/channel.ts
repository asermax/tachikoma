import { join } from "node:path";
import type { Bot } from "grammy";
import type { Message } from "grammy/types";

import type { Channel, ChannelRuntime, Delivery, Exchange } from "../../channels/types.ts";
import type { Logger } from "../../log.ts";
import { unpackCallbackData } from "./buttons.ts";
import { mapButtonTap, mapMediaMessage, mapTextMessage } from "./inbound.ts";
import {
  buildAttachment,
  downloadMedia,
  generateMediaFilename,
  MediaTooLargeError,
  resolveMedia,
} from "./media.ts";
import { Mutex } from "./mutex.ts";
import { deliverText, startTyping } from "./sending.ts";
import { StreamRenderer } from "./streaming.ts";

export interface TelegramChannelOptions {
  chatId: number;
  allowMedia: boolean;
  pushNotifications: boolean;
  mediaDir: string;
  /** Abort the in-flight agent run — wired from sessions.abortExchange. */
  stop: () => Promise<void>;
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

    this.bot.catch(({ error }) => {
      runtime.log.error({ err: error }, "telegram update handling failed");
    });

    // init() validates the token via getMe before going live; start() resolves
    // only when polling stops, so it runs detached.
    await this.bot.init();

    void this.bot
      .start({
        allowed_updates: ["message", "callback_query"],
        onStart: (me) => runtime.log.info({ username: me.username }, "telegram bot polling"),
      })
      .catch((error) => runtime.log.error({ err: error }, "telegram polling crashed"));
  }

  async respond({ events }: Exchange): Promise<void> {
    const log = this.log();

    await this.mutex.run(async () => {
      const stopTyping = startTyping(this.bot.api, this.options.chatId, log);
      const renderer = new StreamRenderer(this.bot.api, this.options.chatId, log);
      this.activeRenderer = renderer;

      try {
        for await (const event of events) {
          switch (event.kind) {
            case "text":
              await renderer.appendText(event.text);
              break;

            case "tool-start":
              await renderer.showTransient(`⚙ ${event.toolName}`);
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

      this.lastOutboundId = (await renderer.finalize()) ?? this.lastOutboundId;
    });
  }

  async deliver(delivery: Delivery): Promise<void> {
    const log = this.log();

    await this.mutex.run(async () => {
      const lastId = await deliverText(
        this.bot.api,
        this.options.chatId,
        delivery.text,
        this.options.pushNotifications,
        log,
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

    // No streaming message to surface the line in — show liveness via typing.
    void this.bot.api
      .sendChatAction(this.options.chatId, "typing")
      .catch((error) => this.log().debug({ err: error }, "status typing action failed"));
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
    this.runtimeOrThrow().submit(inbound);
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

    try {
      const destPath = join(this.options.mediaDir, generateMediaFilename(resolved));
      await downloadMedia(this.bot.api, this.bot.token, resolved, destPath);

      this.runtimeOrThrow().submit(mapMediaMessage(message, buildAttachment(resolved, destPath)));
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

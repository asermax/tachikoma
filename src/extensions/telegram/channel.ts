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
import { deliverText, sendChunked, startTyping } from "./sending.ts";

export interface TelegramChannelOptions {
  chatId: number;
  allowMedia: boolean;
  pushNotifications: boolean;
  mediaDir: string;
}

export class TelegramChannel implements Channel {
  readonly name = "telegram";

  private readonly bot: Bot;
  private readonly options: TelegramChannelOptions;
  private readonly mutex = new Mutex();
  private runtime: ChannelRuntime | null = null;
  private lastInboundId: number | null = null;
  private lastOutboundId: number | null = null;

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
        this.handleText(ctx.message);
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
      let text = "";

      try {
        for await (const event of events) {
          switch (event.kind) {
            case "text":
              text += event.text;
              break;

            case "error":
              await this.sendErrorNotice(event.message, log);
              break;

            default:
              break;
          }
        }
      } finally {
        stopTyping();
      }

      if (text.trim().length > 0) {
        const ids = await sendChunked(this.bot.api, this.options.chatId, text);
        this.lastOutboundId = ids.at(-1) ?? this.lastOutboundId;
      }
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

  async stop(): Promise<void> {
    if (this.bot.isRunning()) await this.bot.stop();
  }

  private handleText(message: Message): void {
    const inbound = mapTextMessage(message);
    if (inbound == null) return;

    this.lastInboundId = message.message_id;
    this.runtimeOrThrow().submit(inbound);
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

  private async sendErrorNotice(message: string, log: Logger): Promise<void> {
    await this.bot.api
      .sendMessage(this.options.chatId, `⚠️ Error: ${message}`)
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

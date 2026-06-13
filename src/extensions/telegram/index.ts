import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { autoRetry } from "@grammyjs/auto-retry";
import { Bot } from "grammy";
import { type Static, Type } from "typebox";

import { expandHome } from "../../workspace.ts";
import { defineExtension } from "../api.ts";
import { type ChannelMessageStore, TelegramChannel } from "./channel.ts";
import { CHANNEL_NAME } from "./inbound.ts";
import { ensureMediaDir } from "./media.ts";
import { registerTelegramTools } from "./tools.ts";

const TelegramConfigSchema = Type.Object({
  botToken: Type.String({ default: "", description: "Telegram bot token from @BotFather" }),
  chatId: Type.Number({ default: 0, description: "Authorized Telegram chat ID" }),
  allowMedia: Type.Boolean({ default: true, description: "Accept inbound media attachments" }),
  pushNotifications: Type.Boolean({
    default: true,
    description: "Fire push notifications for background deliveries via copy+delete",
  }),
  extraFileRoots: Type.Array(Type.String(), {
    default: [],
    description: "Extra absolute roots send_telegram_file may read from",
  }),
});

type TelegramConfig = Static<typeof TelegramConfigSchema>;

/**
 * Telegram channel: grammY long-polling bot restricted to a single chat, media
 * download into the workspace data dir, and agent tools for sending files,
 * reactions, pins, and inline-button prompts through the shared bot instance.
 */
export default defineExtension<TelegramConfig>({
  name: "telegram",

  configSchema: TelegramConfigSchema,

  setup(app) {
    const { botToken, chatId, allowMedia, pushNotifications, extraFileRoots } = app.extensionConfig;

    if (botToken === "" || chatId === 0) {
      app.log.info(
        "telegram channel disabled — set botToken and chatId under [extensions.telegram]",
      );
      return;
    }

    const bot = new Bot(botToken);

    // Transparently honor Telegram 429 retry_after across every bot.api.* call
    // (channel sends, streaming edits, tools, media uploads).
    bot.api.config.use(autoRetry());

    const mediaDir = join(app.workspace.dataDir, "media");

    const store: ChannelMessageStore = {
      record: (messageId, sessionId, direction) =>
        app.sessions.recordChannelMessage(CHANNEL_NAME, messageId, sessionId, direction),
      findSessionId: (messageId) =>
        app.sessions.findSessionByMessageId(CHANNEL_NAME, messageId)?.id ?? null,
    };

    const currentSessionId = () => app.sessions.current()?.id ?? null;

    const channel = new TelegramChannel(bot, {
      chatId,
      allowMedia,
      pushNotifications,
      mediaDir,
      stop: () => app.sessions.abortExchange(),
      store,
      currentSessionId,
    });

    app.bootstrap("media-dir", () => ensureMediaDir(mediaDir, app.log));
    app.channels.register(channel);

    const allowedRoots = [
      ...new Set([
        app.workspace.root,
        tmpdir(),
        ...extraFileRoots.map((root) => resolve(expandHome(root))),
      ]),
    ];

    app.agent.use((pi) => {
      registerTelegramTools(pi, {
        api: bot.api,
        chatId,
        workspaceRoot: app.workspace.root,
        allowedRoots,
        getLastInboundMessageId: () => channel.lastInboundMessageId,
        getLastOutboundMessageId: () => channel.lastOutboundMessageId,
        store,
        currentSessionId,
      });
    });
  },
});

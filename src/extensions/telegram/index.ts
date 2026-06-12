import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Bot } from "grammy";
import { type Static, Type } from "typebox";

import { expandHome } from "../../workspace.ts";
import { defineExtension } from "../api.ts";
import { TelegramChannel } from "./channel.ts";
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
    const mediaDir = join(app.workspace.dataDir, "media");
    const channel = new TelegramChannel(bot, { chatId, allowMedia, pushNotifications, mediaDir });

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
      });
    });
  },
});

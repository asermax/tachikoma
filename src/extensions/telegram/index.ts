import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { autoRetry } from "@grammyjs/auto-retry";
import { Bot } from "grammy";
import { type Static, Type } from "typebox";

import { getLeafId } from "../../agent/session-tree.ts";
import { getBranchRecords, nextBranchId } from "../../sessions/trunk.ts";
import { expandHome } from "../../workspace.ts";
import { defineExtension } from "../api.ts";
import { type MessageRouting, TelegramChannel } from "./channel.ts";
import { ensureMediaDir } from "./media.ts";
import { TelegramMessageStore } from "./store.ts";
import { registerTelegramTools } from "./tools.ts";

const TelegramConfigSchema = Type.Object({
  botToken: Type.String({ default: "", description: "Telegram bot token from @BotFather" }),
  chatId: Type.Number({ default: 0, description: "Authorized Telegram chat ID" }),
  allowMedia: Type.Boolean({ default: true, description: "Accept inbound media attachments" }),
  pushNotifications: Type.Boolean({
    default: true,
    description: "Fire one push notification per background delivery (on its last chunk)",
  }),
  pushNotificationMinSeconds: Type.Number({
    default: 10,
    description:
      "Minimum streamed-response duration (seconds) before a completion push is forced; shorter turns stream without a push (the user is assumed to still be watching)",
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
    const {
      botToken,
      chatId,
      allowMedia,
      pushNotifications,
      pushNotificationMinSeconds,
      extraFileRoots,
    } = app.extensionConfig;

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

    const store = new TelegramMessageStore(app.db, app.log);

    // The routing for the message just produced/received: the live trunk leaf entry id (recorded post-
    // append, so it exists) + the live branch id (`nextBranchId`, the same id the branch gets when it
    // later collapses, so an id written mid-branch resolves to the same branch).
    const currentRouting = (): MessageRouting | null => {
      const trunk = app.sessions.activeTrunkSession();
      if (trunk == null) return null;

      const treeEntryId = getLeafId(trunk);
      if (treeEntryId == null) return null;

      return { treeEntryId, branchId: nextBranchId(getBranchRecords(trunk)) };
    };

    const branchLastExchange = (branchId: string): string | null => {
      const trunk = app.sessions.activeTrunkSession();
      if (trunk == null) return null;

      return (
        getBranchRecords(trunk).find((record) => record.branchId === branchId)?.lastExchange ?? null
      );
    };

    const channel = new TelegramChannel(bot, {
      chatId,
      allowMedia,
      pushNotifications,
      pushNotificationMinSeconds,
      mediaDir,
      stop: () => app.sessions.abortExchange(),
      store,
      currentRouting,
      branchLastExchange,
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
        log: app.log,
        chatId,
        workspaceRoot: app.workspace.root,
        allowedRoots,
        getLastInboundMessageId: () => channel.lastInboundMessageId,
        getLastOutboundMessageId: () => channel.lastOutboundMessageId,
        store,
        currentRouting,
      });
    });
  },
});

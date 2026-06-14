import { and, eq } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { ChannelMessageStore } from "./channel.ts";
import { CHANNEL_NAME } from "./inbound.ts";
import { type ChannelMessageDirection, channelMessages } from "./schema.ts";

/**
 * Telegram-owned reply-to mapping store over the channel_messages table. Records
 * message id ↔ session mappings and resolves a replied-to message back to its
 * owning session id — the only thing reply routing needs, so the lookup reads the
 * session id straight off the mapping row rather than round-tripping the registry.
 */
export class TelegramMessageStore implements ChannelMessageStore {
  private readonly db: AppDatabase;

  constructor(db: AppDatabase) {
    this.db = db;
  }

  record(messageId: string, sessionId: number, direction: ChannelMessageDirection): void {
    this.db
      .insert(channelMessages)
      .values({ channel: CHANNEL_NAME, messageId, sessionId, direction, createdAt: new Date() })
      .onConflictDoUpdate({
        target: [channelMessages.channel, channelMessages.messageId],
        set: { sessionId, direction },
      })
      .run();
  }

  findSessionId(messageId: string): number | null {
    return (
      this.db
        .select({ sessionId: channelMessages.sessionId })
        .from(channelMessages)
        .where(
          and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
        )
        .get()?.sessionId ?? null
    );
  }
}

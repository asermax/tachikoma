import { and, eq } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { Logger } from "../../log.ts";
import type { ChannelMessageStore, MessageRouting } from "./channel.ts";
import { CHANNEL_NAME } from "./inbound.ts";
import { type ChannelMessageDirection, channelMessages } from "./schema.ts";

/**
 * Telegram-owned routing store over the channel_messages table (daily-trunk model). Records
 * a Telegram message id → the trunk tree entry + branch it corresponds to, and resolves a referenced
 * message back to that mapping. Reply/reaction/button handling uses the resolved branch to force an
 * append (same branch) or a shift (an earlier branch) without invoking the topic classifier.
 */
export class TelegramMessageStore implements ChannelMessageStore {
  private readonly db: AppDatabase;
  private readonly log: Logger;

  constructor(db: AppDatabase, log: Logger) {
    this.db = db;
    this.log = log;
  }

  record(messageId: string, routing: MessageRouting, direction: ChannelMessageDirection): void {
    this.db
      .insert(channelMessages)
      .values({
        channel: CHANNEL_NAME,
        messageId,
        treeEntryId: routing.treeEntryId,
        branchId: routing.branchId,
        direction,
        createdAt: new Date(),
      })
      // Re-point the mapping on conflict (e.g. an outbound id re-recorded after a later branch shift).
      .onConflictDoUpdate({
        target: [channelMessages.channel, channelMessages.messageId],
        set: { treeEntryId: routing.treeEntryId, branchId: routing.branchId, direction },
      })
      .run();
  }

  resolve(messageId: string): MessageRouting | null {
    const row = this.db
      .select({
        treeEntryId: channelMessages.treeEntryId,
        branchId: channelMessages.branchId,
      })
      .from(channelMessages)
      .where(
        and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
      )
      .get();

    if (row == null) {
      this.log.debug({ messageId }, "no channel routing for telegram message");
      return null;
    }

    return row;
  }
}

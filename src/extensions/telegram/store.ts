import { and, desc, eq } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { Logger } from "../../log.ts";
import type {
  ChannelMessageStore,
  MessageRouting,
  RecordDetails,
  ResolvedChannelMessage,
} from "./channel.ts";
import { CHANNEL_NAME, truncateQuote } from "./inbound.ts";
import {
  type ChannelMessageDirection,
  type ChannelMessageRecord,
  channelMessages,
} from "./schema.ts";

/**
 * Telegram-owned ledger over the channel_messages table (daily-trunk model). Records
 * every final Telegram message sent or received while a trunk is active — its trunk
 * routing, a bounded content label, and an in-conversation flag — and resolves a
 * referenced message back to that row. Reply/reaction/button handling uses the resolved
 * branch to force an append (same branch) or a shift (an earlier branch) without
 * invoking the topic classifier; the label backs the identifying quote.
 */
export class TelegramMessageStore implements ChannelMessageStore {
  private readonly db: AppDatabase;
  private readonly log: Logger;

  constructor(db: AppDatabase, log: Logger) {
    this.db = db;
    this.log = log;
  }

  record(
    messageId: string,
    routing: MessageRouting,
    direction: ChannelMessageDirection,
    details?: RecordDetails,
  ): void {
    // Blank labels carry no quote value and normalize to null; non-blank labels are bounded
    // by the shared quote truncation so a row never holds unbounded content.
    const label =
      details?.label != null && details.label.trim().length > 0
        ? truncateQuote(details.label)
        : null;

    // A recording failure must never break a send that already succeeded: log and skip.
    try {
      this.db
        .insert(channelMessages)
        .values({
          channel: CHANNEL_NAME,
          messageId,
          treeEntryId: routing.treeEntryId,
          branchId: routing.branchId,
          direction,
          label,
          inConversation: details?.inConversation ?? true,
          createdAt: new Date(),
        })
        // Re-point the mapping on conflict (e.g. an outbound id re-recorded after a later branch
        // shift). Label and inConversation enter the update only when supplied, so a re-record
        // can never clobber a previously written label (a button prompt keeps its label across
        // the tap turn that references it).
        .onConflictDoUpdate({
          target: [channelMessages.channel, channelMessages.messageId],
          set: {
            treeEntryId: routing.treeEntryId,
            branchId: routing.branchId,
            direction,
            ...(label != null ? { label } : {}),
            ...(details?.inConversation != null ? { inConversation: details.inConversation } : {}),
          },
        })
        .run();
    } catch (error) {
      this.log.warn({ err: error, messageId, direction }, "recording channel message failed");
    }
  }

  resolve(messageId: string): ResolvedChannelMessage | null {
    const row = this.fetchRow(messageId);
    if (row == null) {
      this.log.debug({ messageId }, "no channel routing for telegram message");
      return null;
    }

    return { treeEntryId: row.treeEntryId, branchId: row.branchId, label: row.label };
  }

  /**
   * Whether the message is the bottom-most row of its side of the exchange — in-conversation
   * and the newest such row of its direction on its tree entry (the final chunk of the
   * outgoing side, the inbound message of the incoming side). Ordered by (createdAt, id) so
   * same-second writes resolve deterministically by insertion order. Out-of-conversation rows
   * (deliveries, notices) never qualify: they may sit at the bottom of the chat but not of
   * the conversation.
   */
  isConversationBottom(messageId: string): boolean {
    const row = this.fetchRow(messageId);
    if (row == null || !row.inConversation) return false;

    const latest = this.db
      .select({ id: channelMessages.id })
      .from(channelMessages)
      .where(
        and(
          eq(channelMessages.channel, CHANNEL_NAME),
          eq(channelMessages.treeEntryId, row.treeEntryId),
          eq(channelMessages.direction, row.direction),
          eq(channelMessages.inConversation, true),
        ),
      )
      .orderBy(desc(channelMessages.createdAt), desc(channelMessages.id))
      .limit(1)
      .get();

    return latest?.id === row.id;
  }

  private fetchRow(messageId: string): ChannelMessageRecord | null {
    return (
      this.db
        .select()
        .from(channelMessages)
        .where(
          and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
        )
        .get() ?? null
    );
  }
}

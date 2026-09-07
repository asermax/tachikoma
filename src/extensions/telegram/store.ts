import { and, desc, eq } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { Logger } from "../../log.ts";
import type { ChannelMessageStore, ResolvedChannelMessage } from "./channel.ts";
import { CHANNEL_NAME, truncateQuote } from "./inbound.ts";
import {
  type ChannelMessageDirection,
  type ChannelMessageRecord,
  channelMessages,
} from "./schema.ts";

/** Optional per-message ledger details; omitted fields never overwrite on re-record. */
export interface RecordDetails {
  /** Bounded content label for reaction/reply quotes (blank normalizes to null). */
  label?: string | null;
  /** Whether the message belongs to the conversation surface (default true). */
  inConversation?: boolean;
}

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
    routing: { treeEntryId: string; branchId: string },
    direction: ChannelMessageDirection,
    details?: RecordDetails,
  ): void {
    // Blank labels carry no quote value and normalize to null; non-blank labels are bounded
    // by the shared quote truncation so a row never holds unbounded content.
    const label =
      details?.label != null && details.label.trim().length > 0
        ? truncateQuote(details.label)
        : null;

    this.db
      .insert(channelMessages)
      .values({
        channel: CHANNEL_NAME,
        messageId,
        treeEntryId: routing.treeEntryId,
        branchId: routing.branchId,
        direction,
        ...(label != null ? { label } : {}),
        ...(details?.inConversation != null ? { inConversation: details.inConversation } : {}),
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
  }

  resolve(messageId: string): ResolvedChannelMessage | null {
    const row = this.db
      .select()
      .from(channelMessages)
      .where(
        and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
      )
      .get();

    if (row == null) {
      this.log.debug({ messageId }, "no channel routing for telegram message");
      return null;
    }

    return {
      treeEntryId: row.treeEntryId,
      branchId: row.branchId,
      label: row.label,
      inConversation: row.inConversation,
      isLatestInConversation: this.isLatestInConversation(row),
    };
  }

  /**
   * Whether `row` is the newest in-conversation row of its direction on its tree entry — a
   * bottom-most message of that exchange's side (the final chunk of the outgoing side, the
   * inbound message of the incoming side). Ordered by (createdAt, id) so same-second writes
   * resolve deterministically by insertion order. Out-of-conversation rows (deliveries,
   * notices) never qualify: they may sit at the bottom of the chat but not of the conversation.
   */
  private isLatestInConversation(row: ChannelMessageRecord): boolean {
    if (!row.inConversation) return false;

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
}

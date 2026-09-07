import { index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const CHANNEL_MESSAGE_DIRECTIONS = {
  incoming: "incoming",
  outgoing: "outgoing",
} as const;

export type ChannelMessageDirection = keyof typeof CHANNEL_MESSAGE_DIRECTIONS;

// Maps a channel's message ids to the trunk tree entry + branch they produced or
// received (daily-trunk model), so a reply/reaction/button can be
// force-routed to its owning branch (same branch → append, earlier branch →
// forced shift + context injection).
export const channelMessages = sqliteTable(
  "channel_messages",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    channel: text("channel").notNull(),
    messageId: text("message_id").notNull(),
    /** The pi session-tree entry id this channel message corresponds to. */
    treeEntryId: text("tree_entry_id").notNull(),
    /** The deterministic `topic-N` branch id the entry belonged to when recorded. */
    branchId: text("branch_id").notNull(),
    direction: text("direction").$type<ChannelMessageDirection>().notNull(),
    /**
     * Bounded content of the message (R19): the final text a streamed chunk settled on, the sent
     * body of a tool message/delivery/notice, or the inbound message text — null when blank or for
     * pre-ledger rows. Recovered as a reaction/reply quote; the trunk session file remains the
     * conversational source of truth (ADR-014), this is channel-content metadata only.
     */
    label: text("label"),
    /**
     * Whether the message is part of the conversation surface at its recorded position (R18/R21):
     * exchange chunks, tool sends, and inbound messages are in-conversation; background deliveries
     * and one-off notices are not — they surface out-of-band even when their routing matches the
     * live tip, so quote suppression never applies to them.
     */
    inConversation: integer("in_conversation", { mode: "boolean" }).notNull().default(true),
    createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
  },
  (table) => [
    uniqueIndex("ux_channel_messages_channel_message").on(table.channel, table.messageId),
    index("ix_channel_messages_branch").on(table.branchId),
  ],
);

export type ChannelMessageRecord = typeof channelMessages.$inferSelect;

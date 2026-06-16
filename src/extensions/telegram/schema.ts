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
    createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
  },
  (table) => [
    uniqueIndex("ux_channel_messages_channel_message").on(table.channel, table.messageId),
    index("ix_channel_messages_branch").on(table.branchId),
  ],
);

export type ChannelMessageRecord = typeof channelMessages.$inferSelect;

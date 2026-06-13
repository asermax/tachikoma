import { index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const CHANNEL_MESSAGE_DIRECTIONS = {
  incoming: "incoming",
  outgoing: "outgoing",
} as const;

export type ChannelMessageDirection = keyof typeof CHANNEL_MESSAGE_DIRECTIONS;

// Maps a channel's message ids back to the session that produced or received
// them, so a reply-to can be force-routed to its owning session.
export const channelMessages = sqliteTable(
  "channel_messages",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    channel: text("channel").notNull(),
    messageId: text("message_id").notNull(),
    sessionId: integer("session_id").notNull(),
    direction: text("direction").$type<ChannelMessageDirection>().notNull(),
    createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
  },
  (table) => [
    uniqueIndex("ux_channel_messages_channel_message").on(table.channel, table.messageId),
    index("ix_channel_messages_session").on(table.sessionId),
  ],
);

export type ChannelMessageRecord = typeof channelMessages.$inferSelect;

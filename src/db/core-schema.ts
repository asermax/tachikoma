import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const sessions = sqliteTable("sessions", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  channel: text("channel").notNull(),
  piSessionFile: text("pi_session_file"),
  summary: text("summary"),
  lastExchange: text("last_exchange"),
  createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
  closedAt: integer("closed_at", { mode: "timestamp" }),
  lastResumedAt: integer("last_resumed_at", { mode: "timestamp" }),
  postProcessingState: text("post_processing_state", { mode: "json" }).$type<
    Record<string, "completed" | "failed">
  >(),
});

export type SessionRecord = typeof sessions.$inferSelect;

export const appState = sqliteTable(
  "app_state",
  {
    namespace: text("namespace").notNull(),
    key: text("key").notNull(),
    value: text("value", { mode: "json" }),
    updatedAt: integer("updated_at", { mode: "timestamp" }).notNull(),
  },
  (table) => [primaryKey({ columns: [table.namespace, table.key] })],
);

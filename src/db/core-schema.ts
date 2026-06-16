import { integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

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

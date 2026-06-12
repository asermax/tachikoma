import { join } from "node:path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

import * as schema from "./schema.ts";

export type AppDatabase = ReturnType<typeof createDatabase>;

export const createDatabase = (file: string) => {
  const sqlite = new Database(file);
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");

  return drizzle(sqlite, { schema });
};

export const migrationsFolder = (): string => join(import.meta.dirname, "..", "..", "drizzle");

export const runMigrations = (db: AppDatabase, folder = migrationsFolder()): void => {
  migrate(db, { migrationsFolder: folder });
};

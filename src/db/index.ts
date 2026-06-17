import { join } from "node:path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";

import type { Logger } from "../log.ts";
import * as schema from "./schema.ts";

export type AppDatabase = ReturnType<typeof createDatabase>;

export const createDatabase = (file: string, log?: Logger) => {
  const sqlite = new Database(file);
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  // Background tasks, the maintenance tick, and the coordinator can all touch
  // the DB concurrently; wait out a transient writer lock instead of throwing.
  sqlite.pragma("busy_timeout = 5000");

  log?.debug({ file }, "opened database");

  return drizzle(sqlite, { schema });
};

export const migrationsFolder = (): string => join(import.meta.dirname, "..", "..", "drizzle");

export const runMigrations = (db: AppDatabase, log?: Logger, folder = migrationsFolder()): void => {
  log?.info({ folder }, "applying migrations");

  const startedAt = Date.now();

  try {
    migrate(db, { migrationsFolder: folder });
  } catch (err) {
    log?.error({ err, folder }, "migration failed");
    throw err;
  }

  log?.info({ folder, durationMs: Date.now() - startedAt }, "migrations applied");
};

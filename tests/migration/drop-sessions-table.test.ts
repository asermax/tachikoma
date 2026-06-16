import { cpSync, existsSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it, vi } from "vitest";

import { createDatabase, migrationsFolder, runMigrations } from "../../src/db/index.ts";
import type { Logger } from "../../src/log.ts";
import {
  backupBeforeSessionsDrop,
  SESSIONS_DROP_BACKUP_DB,
} from "../../src/migration/drop-sessions-table.ts";
import { Workspace } from "../../src/workspace.ts";

const fakeLog = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const makeWorkspace = async (): Promise<Workspace> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-drop-sessions-"));
  const workspace = new Workspace(dir);
  await workspace.ensure();
  return workspace;
};

/** A migrations folder holding only the pre-DLT-175 migrations (everything before 0008). */
const preRefactorMigrationsFolder = (): string => {
  const source = migrationsFolder();
  const target = mkdtempSync(join(tmpdir(), "tachi-migrations-pre175-"));

  for (const entry of readdirSync(source)) {
    if (entry.startsWith("0008")) continue;
    cpSync(join(source, entry), join(target, entry), { recursive: true });
  }

  // Drop the 0008 entry from the copied journal so drizzle treats the pre-refactor schema as current.
  const journalPath = join(target, "meta", "_journal.json");
  const journal = JSON.parse(readFileSync(journalPath, "utf8"));
  journal.entries = journal.entries.filter((e: { tag: string }) => !e.tag.startsWith("0008"));
  writeFileSync(journalPath, JSON.stringify(journal));

  return target;
};

/** Seed a database at the pre-DLT-175 schema (sessions present, old channel_messages shape). */
const seedOldSchema = (file: string): void => {
  const db = createDatabase(file);
  runMigrations(db, preRefactorMigrationsFolder());

  const raw = new Database(file);
  raw.prepare("INSERT INTO sessions (channel, created_at, error) VALUES ('telegram', 0, 0)").run();
  raw
    .prepare(
      "INSERT INTO channel_messages (channel, message_id, session_id, direction, created_at) VALUES ('telegram', 'm-1', 1, 'incoming', 0)",
    )
    .run();
  raw
    .prepare(
      "INSERT INTO app_state (namespace, key, value, updated_at) VALUES ('keep', 'k', '1', 0)",
    )
    .run();
  raw.close();
};

const tableColumns = (file: string, table: string): string[] => {
  const raw = new Database(file, { readonly: true });
  try {
    return raw
      .prepare(`PRAGMA table_info(${table})`)
      .all()
      .map((c) => (c as { name: string }).name);
  } finally {
    raw.close();
  }
};

const tableExists = (file: string, table: string): boolean => {
  const raw = new Database(file, { readonly: true });
  try {
    return (
      raw.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name = ?").all(table)
        .length > 0
    );
  } finally {
    raw.close();
  }
};

describe("backupBeforeSessionsDrop + drizzle 0008", () => {
  it("backs up, drops sessions, and reshapes channel_messages while keeping app_state", async () => {
    const workspace = await makeWorkspace();
    seedOldSchema(workspace.databaseFile);
    const backup = join(workspace.dataDir, SESSIONS_DROP_BACKUP_DB);

    await backupBeforeSessionsDrop(workspace, fakeLog);
    expect(existsSync(backup)).toBe(true);

    // The live DB then has the final migration applied (as runApp does after the pre-DB step).
    runMigrations(createDatabase(workspace.databaseFile));

    expect(tableExists(workspace.databaseFile, "sessions")).toBe(false);
    expect(tableColumns(workspace.databaseFile, "channel_messages").sort()).toEqual(
      [
        "branch_id",
        "channel",
        "created_at",
        "direction",
        "id",
        "message_id",
        "tree_entry_id",
      ].sort(),
    );

    // app_state is untouched by the reshape.
    const raw = new Database(workspace.databaseFile, { readonly: true });
    const kept = raw
      .prepare("SELECT value FROM app_state WHERE namespace='keep' AND key='k'")
      .get();
    raw.close();
    expect(kept).not.toBeUndefined();

    // The backup retains the old schema for recovery.
    expect(tableExists(backup, "sessions")).toBe(true);
  });

  it("is an idempotent no-op once the new schema is present", async () => {
    const workspace = await makeWorkspace();
    seedOldSchema(workspace.databaseFile);
    const backup = join(workspace.dataDir, SESSIONS_DROP_BACKUP_DB);

    await backupBeforeSessionsDrop(workspace, fakeLog);
    runMigrations(createDatabase(workspace.databaseFile));

    // A second startup: sessions is gone, so the step must not back up again or error.
    await expect(backupBeforeSessionsDrop(workspace, fakeLog)).resolves.toBeUndefined();

    // No second backup is created (the first remains, untouched).
    expect(existsSync(backup)).toBe(true);
    expect(existsSync(`${backup}.2`)).toBe(false);
  });
});

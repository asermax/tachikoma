import { existsSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it, vi } from "vitest";

import type { Logger } from "../../src/log.ts";
import { adaptLegacyDatabase, LEGACY_BACKUP_DB } from "../../src/migration/database.ts";
import { Workspace } from "../../src/workspace.ts";

const fakeLog = Object.assign(
  { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  { child: () => fakeLog },
) as unknown as Logger;

const makeWorkspace = async (): Promise<Workspace> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-migration-db-"));
  const workspace = new Workspace(dir);
  await workspace.ensure();
  return workspace;
};

const createDb = (file: string, tables: string[]): void => {
  const db = new Database(file);

  for (const table of tables) {
    db.exec(`CREATE TABLE ${table} (id INTEGER PRIMARY KEY)`);
  }

  db.close();
};

describe("adaptLegacyDatabase", () => {
  it("renames an alembic-era database to the legacy backup", async () => {
    const workspace = await makeWorkspace();
    createDb(workspace.databaseFile, ["alembic_version", "sessions"]);

    await adaptLegacyDatabase(workspace, fakeLog);

    expect(existsSync(workspace.databaseFile)).toBe(false);
    expect(existsSync(join(workspace.dataDir, LEGACY_BACKUP_DB))).toBe(true);
  });

  it("renames a schema_migrations-era database to the legacy backup", async () => {
    const workspace = await makeWorkspace();
    createDb(workspace.databaseFile, ["schema_migrations", "tasks"]);

    await adaptLegacyDatabase(workspace, fakeLog);

    expect(existsSync(workspace.databaseFile)).toBe(false);
    expect(existsSync(join(workspace.dataDir, LEGACY_BACKUP_DB))).toBe(true);
  });

  it("leaves a drizzle-era database untouched", async () => {
    const workspace = await makeWorkspace();
    createDb(workspace.databaseFile, ["__drizzle_migrations", "sessions"]);

    await adaptLegacyDatabase(workspace, fakeLog);

    expect(existsSync(workspace.databaseFile)).toBe(true);
    expect(existsSync(join(workspace.dataDir, LEGACY_BACKUP_DB))).toBe(false);
  });

  it("is a no-op when no database exists", async () => {
    const workspace = await makeWorkspace();

    await adaptLegacyDatabase(workspace, fakeLog);

    expect(existsSync(workspace.databaseFile)).toBe(false);
  });

  it("is idempotent: a fresh database after the rename is untouched", async () => {
    const workspace = await makeWorkspace();
    createDb(workspace.databaseFile, ["alembic_version"]);

    await adaptLegacyDatabase(workspace, fakeLog);
    createDb(workspace.databaseFile, ["__drizzle_migrations"]);
    await adaptLegacyDatabase(workspace, fakeLog);

    expect(existsSync(workspace.databaseFile)).toBe(true);
    expect(existsSync(join(workspace.dataDir, LEGACY_BACKUP_DB))).toBe(true);
  });

  it("never overwrites an existing backup", async () => {
    const workspace = await makeWorkspace();
    const backup = join(workspace.dataDir, LEGACY_BACKUP_DB);
    createDb(backup, ["alembic_version"]);
    createDb(workspace.databaseFile, ["schema_migrations"]);

    await adaptLegacyDatabase(workspace, fakeLog);

    expect(existsSync(workspace.databaseFile)).toBe(true);
    expect(existsSync(backup)).toBe(true);
  });
});

import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it, vi } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { taskDefinitions } from "../../src/extensions/tasks/schema.ts";
import type { Logger } from "../../src/log.ts";
import { LEGACY_BACKUP_DB } from "../../src/migration/database.ts";
import { adaptLegacyTasks } from "../../src/migration/tasks.ts";
import { Workspace } from "../../src/workspace.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

interface LegacyDef {
  id: string;
  name: string;
  schedule: string;
  task_type: string;
  prompt: string;
  enabled: number;
  last_fired_at: string | null;
  since: string | null;
  created_at: string | null;
}

const setup = async (
  defs: LegacyDef[] | null,
): Promise<{ workspace: Workspace; db: AppDatabase }> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-migration-tasks-"));
  const workspace = new Workspace(dir);
  await workspace.ensure();

  if (defs != null) {
    const backup = new Database(join(workspace.dataDir, LEGACY_BACKUP_DB));
    backup.exec(`CREATE TABLE task_definitions (
      id TEXT PRIMARY KEY, name TEXT, schedule TEXT, task_type TEXT, prompt TEXT,
      enabled INTEGER, last_fired_at TEXT, since TEXT, created_at TEXT, skills TEXT
    )`);
    const insert = backup.prepare(
      `INSERT INTO task_definitions
       (id, name, schedule, task_type, prompt, enabled, last_fired_at, since, created_at, skills)
       VALUES (@id, @name, @schedule, @task_type, @prompt, @enabled, @last_fired_at, @since, @created_at, '[]')`,
    );
    for (const def of defs) insert.run(def);
    backup.close();
  }

  const db = createDatabase(workspace.databaseFile);
  runMigrations(db);

  return { workspace, db };
};

const cronDef = (overrides: Partial<LegacyDef> = {}): LegacyDef => ({
  id: "task-1",
  name: "morning briefing",
  schedule: JSON.stringify({ type: "cron", expression: "0 8 * * *" }),
  task_type: "session",
  prompt: "give me the briefing",
  enabled: 1,
  last_fired_at: "2026-06-10T08:00:00.000Z",
  since: "2026-06-01T00:00:00.000Z",
  created_at: "2026-06-01T00:00:00.000Z",
  ...overrides,
});

describe("adaptLegacyTasks", () => {
  it("imports a cron definition preserving id, schedule, and anchors", async () => {
    const { workspace, db } = await setup([cronDef()]);

    await adaptLegacyTasks(db, workspace, fakeLog);

    const rows = db.select().from(taskDefinitions).all();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      id: "task-1",
      name: "morning briefing",
      schedule: { type: "cron", expression: "0 8 * * *" },
      taskType: "session",
      enabled: true,
    });
    expect(rows[0]?.since).toEqual(new Date("2026-06-01T00:00:00.000Z"));
    expect(rows[0]?.lastFiredAt).toEqual(new Date("2026-06-10T08:00:00.000Z"));
  });

  it("translates a once schedule and a disabled flag", async () => {
    const { workspace, db } = await setup([
      cronDef({
        id: "task-2",
        schedule: JSON.stringify({ type: "once", at: "2026-07-01T12:00:00.000Z" }),
        task_type: "background",
        enabled: 0,
        last_fired_at: null,
      }),
    ]);

    await adaptLegacyTasks(db, workspace, fakeLog);

    const row = db.select().from(taskDefinitions).all()[0];
    expect(row?.schedule).toEqual({ type: "once", at: "2026-07-01T12:00:00.000Z" });
    expect(row?.taskType).toBe("background");
    expect(row?.enabled).toBe(false);
    expect(row?.lastFiredAt).toBeNull();
  });

  it("skips definitions with an unrecognized schedule or type", async () => {
    const { workspace, db } = await setup([
      cronDef({ id: "ok" }),
      cronDef({ id: "bad-sched", schedule: "not json at all !!" }),
      cronDef({ id: "bad-type", task_type: "telepathy" }),
    ]);

    await adaptLegacyTasks(db, workspace, fakeLog);

    expect(
      db
        .select()
        .from(taskDefinitions)
        .all()
        .map((r) => r.id),
    ).toEqual(["ok"]);
  });

  it("only imports once — deleted tasks do not reappear on a second run", async () => {
    const { workspace, db } = await setup([cronDef()]);

    await adaptLegacyTasks(db, workspace, fakeLog);
    db.delete(taskDefinitions).run();
    await adaptLegacyTasks(db, workspace, fakeLog);

    expect(db.select().from(taskDefinitions).all()).toHaveLength(0);
  });

  it("is a no-op when no legacy backup exists", async () => {
    const { workspace, db } = await setup(null);

    await adaptLegacyTasks(db, workspace, fakeLog);

    expect(db.select().from(taskDefinitions).all()).toHaveLength(0);
  });

  it("imports a bare ISO instant schedule (oldest installs) as a once task", async () => {
    const { workspace, db } = await setup([
      cronDef({ id: "bare", schedule: "2026-08-01T09:00:00.000Z" }),
    ]);

    await adaptLegacyTasks(db, workspace, fakeLog);

    const row = db.select().from(taskDefinitions).all()[0];
    expect(row?.schedule).toEqual({ type: "once", at: "2026-08-01T09:00:00.000Z" });
  });

  it("imports a JSON-string schedule as a once task", async () => {
    const { workspace, db } = await setup([
      cronDef({ id: "str", schedule: JSON.stringify("2026-09-01T10:00:00.000Z") }),
    ]);

    await adaptLegacyTasks(db, workspace, fakeLog);

    const row = db.select().from(taskDefinitions).all()[0];
    expect(row?.schedule).toEqual({ type: "once", at: "2026-09-01T10:00:00.000Z" });
  });

  it("skips schedules that JSON-parse but cannot be mapped", async () => {
    const { workspace, db } = await setup([
      cronDef({ id: "json-null", schedule: "null" }),
      cronDef({ id: "json-number", schedule: "42" }),
      cronDef({ id: "string-bad-date", schedule: JSON.stringify("not-a-date") }),
      cronDef({ id: "once-bad-at", schedule: JSON.stringify({ type: "once", at: "nope" }) }),
      cronDef({ id: "unknown-type", schedule: JSON.stringify({ type: "weekly" }) }),
    ]);

    await adaptLegacyTasks(db, workspace, fakeLog);

    expect(db.select().from(taskDefinitions).all()).toHaveLength(0);
  });

  it("drops unparseable timestamps and defaults since/createdAt to now", async () => {
    const { workspace, db } = await setup([
      cronDef({
        id: "bad-dates",
        last_fired_at: "garbage",
        since: "garbage",
        created_at: null,
      }),
    ]);

    // Timestamps persist at one-second resolution, so floor the lower bound.
    const before = Math.floor(Date.now() / 1000) * 1000;

    await adaptLegacyTasks(db, workspace, fakeLog);

    const row = db.select().from(taskDefinitions).all()[0];
    expect(row?.lastFiredAt).toBeNull();
    expect(row?.since.getTime()).toBeGreaterThanOrEqual(before);
    expect(row?.createdAt.getTime()).toBeGreaterThanOrEqual(before);
  });

  it("is a no-op when the backup lacks a task_definitions table", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-migration-notable-"));
    const workspace = new Workspace(dir);
    await workspace.ensure();

    const backup = new Database(join(workspace.dataDir, LEGACY_BACKUP_DB));
    backup.exec("CREATE TABLE other (id TEXT)");
    backup.close();

    const db = createDatabase(workspace.databaseFile);
    runMigrations(db);

    await adaptLegacyTasks(db, workspace, fakeLog);

    expect(db.select().from(taskDefinitions).all()).toHaveLength(0);
  });

  it("warns and skips when the backup database cannot be read", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-migration-corrupt-"));
    const workspace = new Workspace(dir);
    await workspace.ensure();

    await writeFile(join(workspace.dataDir, LEGACY_BACKUP_DB), "this is not a sqlite database");

    const db = createDatabase(workspace.databaseFile);
    runMigrations(db);
    const log = {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    } as unknown as Logger;

    await adaptLegacyTasks(db, workspace, log);

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ backupFile: expect.any(String) }),
      "could not read legacy task definitions — skipping import",
    );
    expect(db.select().from(taskDefinitions).all()).toHaveLength(0);
  });

  it("does not warn when there are no legacy rows to report", async () => {
    const { workspace, db } = await setup([]);
    const log = {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    } as unknown as Logger;

    await adaptLegacyTasks(db, workspace, log);

    expect(log.warn).not.toHaveBeenCalled();
    expect(db.select().from(taskDefinitions).all()).toHaveLength(0);
  });
});

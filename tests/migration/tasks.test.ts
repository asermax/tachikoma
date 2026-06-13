import { mkdtemp } from "node:fs/promises";
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

const fakeLog = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;

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
});

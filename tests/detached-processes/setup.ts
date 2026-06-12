import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import { sql } from "drizzle-orm";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import type { ProcessLimiter } from "../../src/extensions/detached-processes/limits.ts";
import type {
  ProcessNotification,
  ReconcileDeps,
} from "../../src/extensions/detached-processes/reconcile.ts";
import {
  type NewDetachedProcess,
  ProcessRepository,
} from "../../src/extensions/detached-processes/repository.ts";
import type { DetachedProcessRecord } from "../../src/extensions/detached-processes/schema.ts";
import type { SpawnDeps } from "../../src/extensions/detached-processes/spawn.ts";
import type { ProcessToolDeps } from "../../src/extensions/detached-processes/tools.ts";
import type { Logger } from "../../src/log.ts";

// DDL mirror of schema.ts — remove once central migrations include these tables
const DETACHED_PROCESSES_DDL = [
  `CREATE TABLE IF NOT EXISTS detached_processes (
    id text PRIMARY KEY NOT NULL,
    name text NOT NULL,
    command text NOT NULL,
    cwd text NOT NULL,
    pid integer NOT NULL,
    status text NOT NULL,
    exit_code integer,
    stop_reason text,
    stdout_path text NOT NULL,
    stderr_path text NOT NULL,
    memory_limit_mb integer,
    started_at integer NOT NULL,
    exited_at integer
  )`,
  "CREATE INDEX IF NOT EXISTS ix_detached_processes_status ON detached_processes (status)",
];

export const fakeLogger = (): Logger =>
  ({ debug: () => {}, info: () => {}, warn: () => {}, error: () => {} }) as unknown as Logger;

export const noLimits: ProcessLimiter = {
  wrap: (command) => ({ file: "sh", args: ["-c", command], limited: false }),
};

export interface TestContext {
  db: AppDatabase;
  repository: ProcessRepository;
  processesDir: string;
  log: Logger;
  notifications: ProcessNotification[];
  reconcile: ReconcileDeps;
  spawnDeps: SpawnDeps;
  toolDeps: ProcessToolDeps;
}

export const createTestContext = async (): Promise<TestContext> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-procs-"));
  const db = createDatabase(join(dir, "test.db"));

  runMigrations(db);

  for (const statement of DETACHED_PROCESSES_DDL) db.run(sql.raw(statement));

  const processesDir = join(dir, "processes");
  await mkdir(processesDir, { recursive: true });

  const repository = new ProcessRepository(db);
  const log = fakeLogger();
  const notifications: ProcessNotification[] = [];

  const reconcile: ReconcileDeps = {
    repository,
    processesDir,
    notify: (notification) => notifications.push(notification),
    log,
  };

  const spawnDeps: SpawnDeps = { repository, limiter: noLimits, processesDir, log };
  const toolDeps: ProcessToolDeps = { ...reconcile, limiter: noLimits, defaultMemoryLimitMb: null };

  return { db, repository, processesDir, log, notifications, reconcile, spawnDeps, toolDeps };
};

/** Insert a running record directly, bypassing spawn — for reconcile-style tests. */
export const insertRunningRecord = (
  context: TestContext,
  pid: number,
  overrides: Partial<NewDetachedProcess> = {},
): DetachedProcessRecord => {
  const id = randomUUID();
  const dir = join(context.processesDir, id);

  return context.repository.create({
    id,
    name: "fixture",
    command: "true",
    cwd: "/tmp",
    pid,
    stdoutPath: join(dir, "stdout.log"),
    stderrPath: join(dir, "stderr.log"),
    memoryLimitMb: null,
    startedAt: new Date(),
    ...overrides,
  });
};

export const waitFor = async (condition: () => boolean, timeoutMs = 3000): Promise<void> => {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (condition()) return;
    await sleep(20);
  }

  throw new Error(`condition not met within ${timeoutMs}ms`);
};

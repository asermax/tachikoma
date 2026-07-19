import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { sql } from "drizzle-orm";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";

// DDL mirror of schema.ts — remove once central migrations include these tables
const TASKS_DDL = [
  `CREATE TABLE IF NOT EXISTS task_definitions (
    id text PRIMARY KEY NOT NULL,
    name text NOT NULL,
    schedule text NOT NULL,
    task_type text NOT NULL,
    prompt text NOT NULL,
    goal text,
    enabled integer DEFAULT true NOT NULL,
    last_fired_at integer,
    since integer NOT NULL,
    created_at integer NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS task_instances (
    id text PRIMARY KEY NOT NULL,
    definition_id text REFERENCES task_definitions(id),
    task_type text NOT NULL,
    status text NOT NULL,
    prompt text NOT NULL,
    goal text,
    scheduled_for integer NOT NULL,
    started_at integer,
    completed_at integer,
    result text,
    question text,
    user_response text,
    resume_context text,
    pi_session_file text,
    updated_at integer NOT NULL,
    created_at integer NOT NULL
  )`,
  "CREATE INDEX IF NOT EXISTS ix_task_instances_status ON task_instances (status)",
  "CREATE INDEX IF NOT EXISTS ix_task_instances_task_type ON task_instances (task_type)",
  // `goal` bridge: migration 0001 already creates these tables, so the CREATE
  // TABLE blocks above are inert (IF NOT EXISTS) and a `goal` column added there
  // never lands. ALTER the migration-created tables instead — exactly how
  // migration 0005 added `pi_session_file`. Removed at landing once the central
  // `0009_tasks_goal` migration is generated and applied by runMigrations.
  "ALTER TABLE task_definitions ADD goal text",
  "ALTER TABLE task_instances ADD goal text",
];

export const createTasksTestDb = async (): Promise<AppDatabase> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-tasks-"));
  const db = createDatabase(join(dir, "test.db"));

  runMigrations(db);

  for (const statement of TASKS_DDL) db.run(sql.raw(statement));

  return db;
};

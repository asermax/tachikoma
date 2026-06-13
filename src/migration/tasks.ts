import { join } from "node:path";
import Database from "better-sqlite3";

import type { AppDatabase } from "../db/index.ts";
import { KeyValueState } from "../db/state.ts";
import {
  type StoredSchedule,
  TASK_TYPES,
  type TaskType,
  taskDefinitions,
} from "../extensions/tasks/schema.ts";
import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { LEGACY_BACKUP_DB } from "./database.ts";
import { pathExists } from "./fs.ts";

const IMPORTED_FLAG = "legacy-task-definitions";

interface LegacyDefinitionRow {
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

const tableExists = (db: Database.Database, name: string): boolean =>
  db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(name) != null;

const parseDate = (value: string | null): Date | null => {
  if (value == null) return null;

  // Legacy timestamps are ISO-ish strings; an unparseable value is dropped
  // rather than poisoning the row with an Invalid Date.
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

/**
 * Translate the legacy schedule blob (a JSON string, or a bare ISO instant in
 * the oldest installs) into the stored union. Returns null for anything that
 * cannot be mapped, so the caller can skip the definition with a warning.
 */
const parseSchedule = (raw: string): StoredSchedule | null => {
  let data: unknown;

  try {
    data = JSON.parse(raw);
  } catch {
    const at = new Date(raw);
    return Number.isNaN(at.getTime()) ? null : { type: "once", at: at.toISOString() };
  }

  if (typeof data === "string") {
    const at = new Date(data);
    return Number.isNaN(at.getTime()) ? null : { type: "once", at: at.toISOString() };
  }

  if (data == null || typeof data !== "object") return null;

  const record = data as { type?: unknown; expression?: unknown; at?: unknown };

  if (record.type === "cron" && typeof record.expression === "string") {
    return { type: "cron", expression: record.expression };
  }

  if (record.type === "once" && typeof record.at === "string") {
    const at = new Date(record.at);
    return Number.isNaN(at.getTime()) ? null : { type: "once", at: at.toISOString() };
  }

  return null;
};

const isTaskType = (value: string): value is TaskType => value in TASK_TYPES;

const readLegacyDefinitions = (backupFile: string): LegacyDefinitionRow[] => {
  const db = new Database(backupFile, { readonly: true, fileMustExist: true });

  try {
    if (!tableExists(db, "task_definitions")) return [];

    return db
      .prepare(
        `SELECT id, name, schedule, task_type, prompt, enabled, last_fired_at, since, created_at
         FROM task_definitions`,
      )
      .all() as LegacyDefinitionRow[];
  } finally {
    db.close();
  }
};

/**
 * Port task definitions from the backed-up legacy database into the live one.
 * Runs after the database is created, reading from the backup the database step
 * left behind. Definitions are durable user intent (schedules); instances and
 * other legacy tables are deliberately not carried over.
 *
 * Idempotent twice over: a one-time flag prevents re-import (so tasks the user
 * later deletes do not reappear), and inserts ignore ids that already exist.
 * Legacy ids and `since`/`created_at` are preserved so cron anchoring carries
 * over; the legacy `skills` pin is dropped (skill pinning no longer exists).
 */
export const adaptLegacyTasks = async (
  db: AppDatabase,
  workspace: Workspace,
  log: Logger,
): Promise<void> => {
  const state = new KeyValueState(db, "migration");
  if (state.get<boolean>(IMPORTED_FLAG) === true) return;

  const backupFile = join(workspace.dataDir, LEGACY_BACKUP_DB);
  if (!(await pathExists(backupFile))) return;

  let rows: LegacyDefinitionRow[];

  try {
    rows = readLegacyDefinitions(backupFile);
  } catch (error) {
    log.warn({ backupFile, error }, "could not read legacy task definitions — skipping import");
    return;
  }

  let imported = 0;
  let skipped = 0;

  for (const row of rows) {
    const schedule = parseSchedule(row.schedule);

    if (schedule == null || !isTaskType(row.task_type)) {
      log.warn(
        { id: row.id, name: row.name, schedule: row.schedule, taskType: row.task_type },
        "skipping legacy task definition with an unrecognized schedule or type",
      );
      skipped += 1;
      continue;
    }

    const result = db
      .insert(taskDefinitions)
      .values({
        id: row.id,
        name: row.name,
        schedule,
        taskType: row.task_type,
        prompt: row.prompt,
        enabled: row.enabled !== 0,
        lastFiredAt: parseDate(row.last_fired_at),
        since: parseDate(row.since) ?? new Date(),
        createdAt: parseDate(row.created_at) ?? new Date(),
      })
      .onConflictDoNothing({ target: taskDefinitions.id })
      .run();

    if (result.changes > 0) imported += 1;
  }

  state.set(IMPORTED_FLAG, true);

  if (imported > 0 || skipped > 0) {
    log.warn(
      { imported, skipped },
      "imported legacy task definitions (schedules preserved; instances and skill pins not carried over)",
    );
  }
};

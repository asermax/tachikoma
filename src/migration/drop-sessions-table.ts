import { copyFile } from "node:fs/promises";
import { join } from "node:path";
import Database from "better-sqlite3";

import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { pathExists } from "./fs.ts";

export const SESSIONS_DROP_BACKUP_DB = "tachikoma.pre-dlt175-backup.db";

// The schema drop is detectable by the presence of the `sessions` table: it existed in every
// pre-refactor schema and is removed by the 0008 migration. Once gone, this step is a no-op.
const SESSIONS_TABLE_PROBE = `
  SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sessions'
`;

const hasSessionsTable = (file: string): boolean => {
  const db = new Database(file, { readonly: true, fileMustExist: true });

  try {
    return db.prepare(SESSIONS_TABLE_PROBE).all().length > 0;
  } finally {
    db.close();
  }
};

/**
 * Pre-DB refactor step for the daily-trunk model: the 0008 drizzle migration drops the `sessions`
 * table and reshapes `channel_messages` destructively. Before drizzle applies it, COPY the database to
 * a backup (unlike the legacy import which renames — here `app_state`/tasks/workflows are kept and the
 * live file keeps being used) so the dropped conversation history stays recoverable. Self-detecting on
 * the `sessions` table and idempotent: once the new schema is present (or a backup already exists), it
 * is a fast no-op.
 */
export const backupBeforeSessionsDrop = async (
  workspace: Workspace,
  log: Logger,
): Promise<void> => {
  const file = workspace.databaseFile;

  if (!(await pathExists(file))) return;

  let needsBackup: boolean;

  try {
    needsBackup = hasSessionsTable(file);
  } catch (error) {
    log.warn(
      { file, error },
      "could not probe database for the pre-trunk schema — leaving it untouched",
    );
    return;
  }

  if (!needsBackup) return;

  const backup = join(workspace.dataDir, SESSIONS_DROP_BACKUP_DB);

  if (await pathExists(backup)) {
    log.warn(
      { file, backup },
      "pre-trunk schema detected but a backup already exists — leaving the backup untouched",
    );
    return;
  }

  // Copy (not rename) so the live DB keeps being used; only the dropped/reshaped tables differ.
  // Sidecar journals come along so the backup stays a recoverable point-in-time copy.
  for (const suffix of ["", "-wal", "-shm"]) {
    if (await pathExists(file + suffix)) await copyFile(file + suffix, backup + suffix);
  }

  log.warn(
    { file, backup },
    "database backed up before the schema change — sessions are dropped and channel_messages reshaped; the backup retains the old tables",
  );
};

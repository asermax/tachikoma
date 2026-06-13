import { rename } from "node:fs/promises";
import { join } from "node:path";
import Database from "better-sqlite3";

import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { pathExists } from "./fs.ts";

export const LEGACY_BACKUP_DB = "tachikoma.legacy-backup.db";

// Early legacy installs carry an alembic_version table; later ones a hand-rolled
// schema_migrations table. Either marker means the file predates the drizzle-era schema.
const LEGACY_MARKER_PROBE = `
  SELECT name FROM sqlite_master
  WHERE type = 'table' AND name IN ('alembic_version', 'schema_migrations')
`;

const hasLegacyMarkers = (file: string): boolean => {
  const db = new Database(file, { readonly: true, fileMustExist: true });

  try {
    return db.prepare(LEGACY_MARKER_PROBE).all().length > 0;
  } finally {
    db.close();
  }
};

/**
 * Detect a database written by a legacy install and rename it to a backup so
 * drizzle starts from a fresh file. The backup is never deleted.
 */
export const adaptLegacyDatabase = async (workspace: Workspace, log: Logger): Promise<void> => {
  const file = workspace.databaseFile;

  if (!(await pathExists(file))) return;

  let legacyEra: boolean;

  try {
    legacyEra = hasLegacyMarkers(file);
  } catch (error) {
    log.warn({ file, error }, "could not probe database for legacy markers — leaving it untouched");
    return;
  }

  if (!legacyEra) return;

  const backup = join(workspace.dataDir, LEGACY_BACKUP_DB);

  if (await pathExists(backup)) {
    log.warn(
      { file, backup },
      "legacy database detected but a backup already exists — leaving both untouched",
    );
    return;
  }

  // Sidecar journals move with the main file so the backup stays recoverable.
  for (const suffix of ["", "-wal", "-shm"]) {
    if (await pathExists(file + suffix)) await rename(file + suffix, backup + suffix);
  }

  log.warn(
    { file, backup },
    "legacy database renamed — previous conversation history and tasks are preserved in the backup but are not migrated to the new database",
  );
};

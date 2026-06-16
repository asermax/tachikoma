import type { AppDatabase } from "../db/index.ts";
import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { type Ask, createAsk } from "./ask.ts";
import { adaptContextFiles } from "./context.ts";
import { adaptLegacyDatabase } from "./database.ts";
import { backupBeforeSessionsDrop } from "./drop-sessions-table.ts";
import { adaptSkillsFrontmatter } from "./skills.ts";
import { adaptLegacyTasks } from "./tasks.ts";

export { type Ask, createAsk } from "./ask.ts";
export { adaptConfig } from "./config.ts";

/**
 * Adapt a workspace last used by a legacy install. Every step is
 * self-detecting and idempotent; a pristine or already-adapted workspace is a
 * fast no-op.
 */
export const adaptWorkspace = async (
  workspace: Workspace,
  log: Logger,
  ask: Ask = createAsk(log),
): Promise<void> => {
  // Must complete before drizzle opens the database file, so failures propagate.
  await adaptLegacyDatabase(workspace, log);

  // Back up before the destructive schema migration (drop sessions, reshape channel_messages).
  // Self-detecting/idempotent; runs after the legacy adaptation so a freshly-imported DB is also guarded.
  await backupBeforeSessionsDrop(workspace, log);

  try {
    await adaptContextFiles(workspace, log);
  } catch (error) {
    log.warn({ error }, "context file adaptation failed — continuing startup");
  }

  try {
    await adaptSkillsFrontmatter(workspace, log, ask);
  } catch (error) {
    log.warn({ error }, "skills frontmatter adaptation failed — continuing startup");
  }
};

/**
 * Database-dependent adaptation, run after drizzle opens the live database and
 * applies migrations (it reads from the backup the database step left behind).
 */
export const adaptWorkspaceData = async (
  db: AppDatabase,
  workspace: Workspace,
  log: Logger,
): Promise<void> => {
  try {
    await adaptLegacyTasks(db, workspace, log);
  } catch (error) {
    log.warn({ error }, "legacy task import failed — continuing startup");
  }
};

import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { type Ask, createAsk } from "./ask.ts";
import { adaptContextFiles } from "./context.ts";
import { adaptLegacyDatabase } from "./database.ts";
import { adaptSkillsFrontmatter } from "./skills.ts";

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

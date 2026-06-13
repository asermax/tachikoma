import { access, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";
import { hasRemote, runGit } from "./git.ts";
import type { RebaseResolver } from "./resolve.ts";
import { SYNC_RESULT, smartPull } from "./sync.ts";

export const COMMITTER_NAME = "Tachikoma";
export const COMMITTER_EMAIL = "tachikoma@local";

export const GITIGNORE_ENTRIES = [".tachikoma/\n"];

const readGitignore = async (workspaceRoot: string): Promise<string> => {
  try {
    return await readFile(join(workspaceRoot, ".gitignore"), "utf8");
  } catch {
    return "";
  }
};

const appendMissingEntries = (existing: string): string => {
  let content = existing;

  for (const entry of GITIGNORE_ENTRIES) {
    if (!content.includes(entry)) {
      const separator = content === "" || content.endsWith("\n") ? "" : "\n";
      content += separator + entry;
    }
  }

  return content;
};

/**
 * Ensure the gitignore entries exist without committing — called on every
 * startup so existing workspaces receive new entries; the commit processor
 * picks the change up at the next session close.
 */
const ensureGitignoreEntries = async (workspaceRoot: string): Promise<void> => {
  const existing = await readGitignore(workspaceRoot);
  const updated = appendMissingEntries(existing);

  if (updated !== existing) {
    await writeFile(join(workspaceRoot, ".gitignore"), updated, "utf8");
  }
};

const initializeRepo = async (workspaceRoot: string, log: Logger): Promise<void> => {
  log.info({ path: workspaceRoot }, "initializing workspace git repo");

  await runGit(workspaceRoot, ["init"]);
  await runGit(workspaceRoot, ["config", "user.name", COMMITTER_NAME]);
  await runGit(workspaceRoot, ["config", "user.email", COMMITTER_EMAIL]);
  // The agent commits unattended — never block on a missing GPG setup.
  await runGit(workspaceRoot, ["config", "commit.gpgsign", "false"]);
  await runGit(workspaceRoot, ["commit", "--allow-empty", "-m", "Initial commit"]);

  await ensureGitignoreEntries(workspaceRoot);
  await runGit(workspaceRoot, ["add", ".gitignore"]);
  await runGit(workspaceRoot, ["commit", "-m", "Add gitignore for workspace exclusions"]);

  log.info("workspace git repo initialized");
};

const syncWorkspace = async (
  workspaceRoot: string,
  log: Logger,
  resolver: RebaseResolver | undefined,
): Promise<void> => {
  try {
    if (!(await hasRemote(workspaceRoot, "origin"))) {
      log.debug("no origin remote configured — skipping workspace sync");
      return;
    }

    const result = await smartPull(workspaceRoot, "origin", "HEAD", log, resolver);

    if (result === SYNC_RESULT.dirtySkipped) {
      log.warn("workspace has uncommitted changes — skipping sync");
    } else if (result === SYNC_RESULT.upToDate) {
      log.debug("workspace already up to date");
    } else if (result === SYNC_RESULT.syncFailed) {
      log.warn("workspace sync failed — continuing with local state");
    } else {
      log.info({ result }, "workspace synced");
    }
  } catch (error) {
    log.warn({ err: error }, "workspace sync failed");
  }
};

/**
 * Bootstrap hook: initialize the workspace as a git repo with a fixed
 * committer identity (idempotent), ensure internal state stays gitignored,
 * and sync with the origin remote when one is configured.
 */
export const initializeWorkspaceRepo = async (
  workspaceRoot: string,
  log: Logger,
  resolver?: RebaseResolver,
): Promise<void> => {
  const gitDirMissing = await access(join(workspaceRoot, ".git")).then(
    () => false,
    () => true,
  );

  if (gitDirMissing) {
    await initializeRepo(workspaceRoot, log);
  }

  await ensureGitignoreEntries(workspaceRoot);
  await syncWorkspace(workspaceRoot, log, resolver);
};

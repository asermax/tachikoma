import { access, readFile, stat } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";

import type { Logger } from "../../log.ts";
import { hasUncommittedChanges, runGit, runGitCapture } from "./git.ts";

export const DIVERGENCE_STATUS = {
  upToDate: "UP_TO_DATE",
  ahead: "AHEAD",
  behind: "BEHIND",
  diverged: "DIVERGED",
} as const;

export type DivergenceStatus = (typeof DIVERGENCE_STATUS)[keyof typeof DIVERGENCE_STATUS];

export const PUSH_RESULT = {
  pushed: "PUSHED",
  nothingToPush: "NOTHING_TO_PUSH",
  rebaseSucceeded: "REBASE_SUCCEEDED",
  pushFailed: "PUSH_FAILED",
  rebaseFailed: "REBASE_FAILED",
} as const;

export type PushResult = (typeof PUSH_RESULT)[keyof typeof PUSH_RESULT];

export const SYNC_RESULT = {
  upToDate: "UP_TO_DATE",
  fastForwarded: "FAST_FORWARDED",
  rebaseSucceeded: "REBASE_SUCCEEDED",
  syncFailed: "SYNC_FAILED",
  dirtySkipped: "DIRTY_SKIPPED",
} as const;

export type SyncResult = (typeof SYNC_RESULT)[keyof typeof SYNC_RESULT];

export const PUSH_SUCCESS: ReadonlySet<PushResult> = new Set([
  PUSH_RESULT.pushed,
  PUSH_RESULT.rebaseSucceeded,
]);

const exists = async (path: string): Promise<boolean> =>
  access(path).then(
    () => true,
    () => false,
  );

/**
 * Resolve the real .git directory, following a gitlink file if present.
 * Submodules and worktrees store `.git` as a file containing `gitdir: <path>`
 * rather than a directory — reading it keeps rebase-state checks working there.
 */
const resolveGitDir = async (cwd: string): Promise<string> => {
  const gitPath = join(cwd, ".git");

  try {
    const stats = await stat(gitPath);

    if (stats.isFile()) {
      const content = (await readFile(gitPath, "utf8")).trim();
      const prefix = "gitdir:";

      if (content.startsWith(prefix)) {
        const target = content.slice(prefix.length).trim();
        return isAbsolute(target) ? target : resolve(cwd, target);
      }
    }
  } catch {
    return gitPath;
  }

  return gitPath;
};

const rebaseInProgress = async (cwd: string): Promise<boolean> => {
  const gitDir = await resolveGitDir(cwd);

  return (
    (await exists(join(gitDir, "rebase-merge"))) || (await exists(join(gitDir, "rebase-apply")))
  );
};

const abortStaleRebase = async (cwd: string, log: Logger): Promise<void> => {
  if (!(await rebaseInProgress(cwd))) return;

  log.warn({ path: cwd }, "stale rebase detected — aborting");

  try {
    await runGit(cwd, ["rebase", "--abort"]);
  } catch (error) {
    log.warn({ path: cwd, err: error }, "failed to abort stale rebase");
  }
};

/**
 * Resolve "HEAD" to the actual local branch name. Remotes don't always have a
 * `refs/remotes/<remote>/HEAD` symbolic ref — repos created via `git init`
 * (rather than cloned) never get one — so passing "HEAD" through to
 * `<remote>/HEAD` breaks the rebase ref and false-positives the
 * `merge-base --is-ancestor` checks into DIVERGED.
 */
const resolveBranch = async (cwd: string, branch: string): Promise<string> => {
  if (branch !== "HEAD") return branch;

  const result = await runGitCapture(cwd, ["symbolic-ref", "--short", "HEAD"]);

  if (result.code === 0 && result.stdout !== "") return result.stdout;

  return branch;
};

/**
 * Classify the relationship between local HEAD and `<remote>/<branch>`.
 * Precondition: `git fetch <remote>` has already run.
 */
export const detectDivergence = async (
  cwd: string,
  remote = "origin",
  branch = "HEAD",
): Promise<DivergenceStatus> => {
  const remoteRef = `${remote}/${branch}`;

  const remoteIsAncestor =
    (await runGitCapture(cwd, ["merge-base", "--is-ancestor", remoteRef, "HEAD"])).code === 0;
  const headIsAncestor =
    (await runGitCapture(cwd, ["merge-base", "--is-ancestor", "HEAD", remoteRef])).code === 0;

  if (remoteIsAncestor && headIsAncestor) return DIVERGENCE_STATUS.upToDate;
  if (remoteIsAncestor) return DIVERGENCE_STATUS.ahead;
  if (headIsAncestor) return DIVERGENCE_STATUS.behind;

  return DIVERGENCE_STATUS.diverged;
};

/**
 * Attempt a naive rebase with --autostash. On conflict, aborts the rebase to
 * restore a clean state and returns false.
 */
const tryNaiveRebase = async (cwd: string, remoteBranch: string, log: Logger): Promise<boolean> => {
  const result = await runGitCapture(cwd, ["rebase", "--autostash", remoteBranch]);

  if (result.code === 0) return true;

  if (await rebaseInProgress(cwd)) {
    try {
      await runGit(cwd, ["rebase", "--abort"]);
    } catch (error) {
      log.warn({ path: cwd, err: error }, "failed to abort rebase after conflict");
    }
  } else {
    log.warn({ path: cwd }, "rebase failed without starting (no conflicts)");
  }

  return false;
};

/**
 * Push local commits with divergence recovery: abort stale rebase → fetch →
 * detect divergence → push directly when ahead, or rebase-then-push when
 * diverged. Conflicting rebases are aborted (local commits preserved) and
 * surface as REBASE_FAILED.
 */
export const smartPush = async (
  cwd: string,
  remote: string,
  branch: string,
  log: Logger,
): Promise<PushResult> => {
  try {
    await abortStaleRebase(cwd, log);
    await runGit(cwd, ["fetch", remote]);

    const resolved = await resolveBranch(cwd, branch);
    const divergence = await detectDivergence(cwd, remote, resolved);

    if (divergence === DIVERGENCE_STATUS.upToDate || divergence === DIVERGENCE_STATUS.behind) {
      return PUSH_RESULT.nothingToPush;
    }

    if (divergence === DIVERGENCE_STATUS.ahead) {
      await runGit(cwd, ["push", remote, "HEAD"]);
      return PUSH_RESULT.pushed;
    }

    const remoteBranch = `${remote}/${resolved}`;

    if (!(await tryNaiveRebase(cwd, remoteBranch, log))) return PUSH_RESULT.rebaseFailed;

    try {
      await runGit(cwd, ["push", remote, "HEAD"]);
      return PUSH_RESULT.rebaseSucceeded;
    } catch (error) {
      log.warn({ path: cwd, err: error }, "push failed after successful rebase");
      return PUSH_RESULT.pushFailed;
    }
  } catch (error) {
    log.warn({ path: cwd, err: error }, "smart push failed");
    return PUSH_RESULT.rebaseFailed;
  }
};

/**
 * Pull remote changes with divergence recovery: skip when dirty, abort stale
 * rebase → fetch → detect divergence → fast-forward when behind, or naive
 * rebase when diverged. Conflicting rebases are aborted and surface as
 * SYNC_FAILED.
 */
export const smartPull = async (
  cwd: string,
  remote: string,
  branch: string,
  log: Logger,
): Promise<SyncResult> => {
  try {
    if (await hasUncommittedChanges(cwd)) {
      log.warn({ path: cwd }, "working tree has uncommitted changes — skipping sync");
      return SYNC_RESULT.dirtySkipped;
    }

    await abortStaleRebase(cwd, log);
    await runGit(cwd, ["fetch", remote]);

    const resolved = await resolveBranch(cwd, branch);
    const divergence = await detectDivergence(cwd, remote, resolved);

    if (divergence === DIVERGENCE_STATUS.upToDate) return SYNC_RESULT.upToDate;
    if (divergence === DIVERGENCE_STATUS.ahead) return SYNC_RESULT.upToDate;

    const remoteBranch = `${remote}/${resolved}`;

    if (divergence === DIVERGENCE_STATUS.behind) {
      await runGit(cwd, ["rebase", remoteBranch]);
      return SYNC_RESULT.fastForwarded;
    }

    if (await tryNaiveRebase(cwd, remoteBranch, log)) return SYNC_RESULT.rebaseSucceeded;

    return SYNC_RESULT.syncFailed;
  } catch (error) {
    log.warn({ path: cwd, err: error }, "smart pull failed");
    return SYNC_RESULT.syncFailed;
  }
};

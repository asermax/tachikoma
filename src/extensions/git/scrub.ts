import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { Logger } from "../../log.ts";
import { hasRemote, hasUncommittedChanges, runGit, runGitCapture } from "./git.ts";

const execFileAsync = promisify(execFile);

export const SCRUB_RESULT = {
  scrubbed: "SCRUBBED",
  scrubbedPushFailed: "SCRUBBED_PUSH_FAILED",
  notInstalled: "FILTER_REPO_NOT_INSTALLED",
  dirtyTree: "DIRTY_TREE",
  noPaths: "NO_PATHS",
  pathsNotFound: "PATHS_NOT_FOUND",
  failed: "FAILED",
} as const;

export type ScrubResultCode = (typeof SCRUB_RESULT)[keyof typeof SCRUB_RESULT];

export interface ScrubOutcome {
  code: ScrubResultCode;
  message: string;
  /** Paths that were requested but absent from history, when code === PATHS_NOT_FOUND. */
  missingPaths?: string[];
}

/** True when `git filter-repo` is available on PATH. */
export const isFilterRepoAvailable = async (cwd: string): Promise<boolean> => {
  try {
    await execFileAsync("git", ["filter-repo", "--version"], { cwd });
    return true;
  } catch {
    return false;
  }
};

const pathsAbsentFromHistory = async (cwd: string, paths: string[]): Promise<string[]> => {
  const missing: string[] = [];

  for (const path of paths) {
    const result = await runGitCapture(cwd, ["log", "-1", "--all", "--", path]);

    if (result.stdout === "") missing.push(path);
  }

  return missing;
};

/**
 * Run `git filter-repo` to purge the given paths from the entire history of the
 * workspace repo. filter-repo removes the `origin` remote as a safety measure;
 * we restore it and force-push when an origin is configured so the rewrite
 * propagates. This is destructive and irreversible.
 *
 * Returns an outcome enum rather than throwing — every failure mode (missing
 * tool, dirty tree, unknown path, push failure) is an explicit, surfaced branch
 * so the tool can report it without aborting the session.
 */
export const scrubPaths = async (
  cwd: string,
  paths: string[],
  log: Logger,
): Promise<ScrubOutcome> => {
  if (paths.length === 0) {
    return { code: SCRUB_RESULT.noPaths, message: "No paths provided to scrub." };
  }

  if (await hasUncommittedChanges(cwd)) {
    return {
      code: SCRUB_RESULT.dirtyTree,
      message:
        "Cannot scrub: the working tree has uncommitted changes. Commit or discard them first.",
    };
  }

  if (!(await isFilterRepoAvailable(cwd))) {
    return {
      code: SCRUB_RESULT.notInstalled,
      message:
        "Cannot scrub: `git filter-repo` is not installed. Install it (e.g. `pip install git-filter-repo` or your distro package) and retry.",
    };
  }

  const missingPaths = await pathsAbsentFromHistory(cwd, paths);

  if (missingPaths.length > 0) {
    return {
      code: SCRUB_RESULT.pathsNotFound,
      message: `Cannot scrub: paths not found in git history: ${missingPaths.join(", ")}. Provide paths that exist in the repository's history.`,
      missingPaths,
    };
  }

  const originUrl = (await runGitCapture(cwd, ["remote", "get-url", "origin"])).stdout;

  const filterArgs = ["filter-repo", "--invert-paths", "--force"];

  for (const path of paths) {
    filterArgs.push("--path", path);
  }

  log.info({ paths }, "running git filter-repo to scrub paths");

  const filter = await runGitCapture(cwd, filterArgs);

  if (filter.code !== 0) {
    log.warn({ err: filter.stderr, code: filter.code }, "git filter-repo failed");

    return {
      code: SCRUB_RESULT.failed,
      message: `git filter-repo failed: ${filter.stderr || `exit code ${filter.code}`}`,
    };
  }

  if (originUrl === "") {
    log.info({ paths }, "scrub completed; no origin remote to push to");

    return {
      code: SCRUB_RESULT.scrubbed,
      message: `Scrubbed paths from history: ${paths.join(", ")}. No origin remote is configured, so nothing was pushed.`,
    };
  }

  // filter-repo strips the remote after a rewrite; restore it before pushing.
  if (!(await hasRemote(cwd, "origin"))) {
    await runGit(cwd, ["remote", "add", "origin", originUrl]);
  }

  const push = await runGitCapture(cwd, ["push", "--force", "origin", "HEAD"]);

  if (push.code !== 0) {
    log.warn({ err: push.stderr }, "force push failed after scrub");

    return {
      code: SCRUB_RESULT.scrubbedPushFailed,
      message: `Scrubbed paths from history (${paths.join(", ")}), but the force-push to origin failed: ${push.stderr || `exit code ${push.code}`}. The local history is rewritten; retry the push manually.`,
    };
  }

  log.info({ paths }, "scrub completed and force-pushed to origin");

  return {
    code: SCRUB_RESULT.scrubbed,
    message: `Scrubbed paths from history and force-pushed to origin: ${paths.join(", ")}.`,
  };
};

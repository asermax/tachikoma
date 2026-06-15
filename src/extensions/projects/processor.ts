import { join } from "node:path";

import type { Completer } from "../../git/commit.ts";
import { PUSH_SUCCESS } from "../../git/sync.ts";
import type { Logger } from "../../log.ts";
import type { GitApi, PostProcessor } from "../api.ts";
import { isAhead, isDirty, listSubmodules } from "./git.ts";

export interface ProjectsProcessorDeps {
  workspaceRoot: string;
  side: Completer;
  git: GitApi;
}

export const projectFallbackMessage = (name: string, now = new Date()): string =>
  `Update ${name} files (${now.toISOString().slice(0, 10)})`;

/**
 * Push a project's commits to `origin` and log the outcome. Shared by the dirty
 * pass (after committing) and the clean-ahead pass (push only), so the
 * `smartPush` + result-classification tail lives in one place.
 */
const pushAndReport = async (
  git: GitApi,
  repoPath: string,
  path: string,
  log: Logger,
  successMessage: string,
  failureMessage: string,
): Promise<void> => {
  const result = await git.smartPush(repoPath, "origin", "HEAD", { log });

  if (PUSH_SUCCESS.has(result)) {
    log.info({ path, result }, successMessage);
  } else {
    log.warn({ path, result }, failureMessage);
  }
};

const commitAndPush = async (
  workspaceRoot: string,
  git: GitApi,
  side: Completer,
  path: string,
  log: Logger,
): Promise<void> => {
  const repoPath = join(workspaceRoot, path);
  const name = path.split("/").at(-1) ?? path;

  const message = await git.commitAll({
    cwd: repoPath,
    side,
    fallbackMessage: projectFallbackMessage(name),
    log,
  });

  if (message != null) log.info({ path, message }, "committed project changes");

  await pushAndReport(
    git,
    repoPath,
    path,
    log,
    "pushed project changes",
    "push failed — changes remain committed locally",
  );
};

/**
 * Push (no commit) a project already ahead of its remote — a background task or
 * earlier exchange may have committed without pushing.
 */
const pushProject = async (
  workspaceRoot: string,
  git: GitApi,
  path: string,
  log: Logger,
): Promise<void> => {
  await pushAndReport(
    git,
    join(workspaceRoot, path),
    path,
    log,
    "pushed ahead project changes",
    "push failed — ahead commits remain local",
  );
};

/**
 * Pre-finalize post-processor: commits and pushes every dirty registered
 * project before the workspace commit runs (so submodule pointer updates land
 * in the same workspace commit pass), then pushes any clean project that still
 * sits ahead of its remote so committed changes never linger across sessions.
 */
export const createProjectsProcessor = ({
  workspaceRoot,
  side,
  git,
}: ProjectsProcessorDeps): PostProcessor => ({
  name: "projects-commit",
  phase: "preFinalize",

  async process({ log }) {
    const submodulePaths = await listSubmodules(workspaceRoot);

    if (submodulePaths.length === 0) {
      log.debug("no submodules found — skipping project processing");
      return;
    }

    const dirtyChecks = await Promise.allSettled(
      submodulePaths.map((path) => isDirty(join(workspaceRoot, path))),
    );

    const dirtyPaths: string[] = [];
    const cleanPaths: string[] = [];

    for (const [index, check] of dirtyChecks.entries()) {
      const path = submodulePaths[index] as string;

      if (check.status === "rejected") {
        log.warn({ path, err: check.reason }, "failed to check submodule status");
      } else if (check.value) {
        dirtyPaths.push(path);
      } else {
        cleanPaths.push(path);
      }
    }

    // Pass 1: commit + push dirty submodules. The commit also pushes any local
    // commits already ahead of the remote.
    if (dirtyPaths.length > 0) {
      log.info({ paths: dirtyPaths }, "processing dirty submodules");

      const results = await Promise.allSettled(
        dirtyPaths.map((path) => commitAndPush(workspaceRoot, git, side, path, log)),
      );

      for (const [index, result] of results.entries()) {
        if (result.status === "rejected") {
          log.warn({ path: dirtyPaths[index], err: result.reason }, "failed to process submodule");
        }
      }
    }

    // Pass 2: a clean tree can still sit ahead of its remote — commits made
    // earlier (a background task, a prior exchange) that nothing has pushed.
    // Push those so project commits never linger across sessions.
    const aheadChecks = await Promise.allSettled(
      cleanPaths.map((path) => isAhead(join(workspaceRoot, path))),
    );

    const aheadPaths: string[] = [];

    for (const [index, check] of aheadChecks.entries()) {
      const path = cleanPaths[index] as string;

      if (check.status === "rejected") {
        log.warn({ path, err: check.reason }, "failed to check if submodule is ahead");
      } else if (check.value) {
        aheadPaths.push(path);
      }
    }

    if (aheadPaths.length === 0) {
      if (dirtyPaths.length === 0) {
        log.debug("no dirty or ahead submodules — skipping project processing");
      }
      return;
    }

    log.info({ paths: aheadPaths }, "pushing clean submodules ahead of their remote");

    const pushResults = await Promise.allSettled(
      aheadPaths.map((path) => pushProject(workspaceRoot, git, path, log)),
    );

    for (const [index, result] of pushResults.entries()) {
      if (result.status === "rejected") {
        log.warn({ path: aheadPaths[index], err: result.reason }, "failed to push ahead submodule");
      }
    }
  },
});

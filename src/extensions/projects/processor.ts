import { join } from "node:path";

import type { Logger } from "../../log.ts";
import type { PostProcessor } from "../api.ts";
import { type Completer, commitAll } from "../git/commit.ts";
import { PUSH_SUCCESS, smartPush } from "../git/sync.ts";
import { isDirty, listSubmodules } from "./git.ts";

export interface ProjectsProcessorDeps {
  workspaceRoot: string;
  side: Completer;
}

export const projectFallbackMessage = (name: string, now = new Date()): string =>
  `Update ${name} files (${now.toISOString().slice(0, 10)})`;

const commitAndPush = async (
  workspaceRoot: string,
  side: Completer,
  path: string,
  log: Logger,
): Promise<void> => {
  const repoPath = join(workspaceRoot, path);
  const name = path.split("/").at(-1) ?? path;

  const message = await commitAll({
    cwd: repoPath,
    side,
    fallbackMessage: projectFallbackMessage(name),
    log,
  });

  if (message != null) log.info({ path, message }, "committed project changes");

  const result = await smartPush(repoPath, "origin", "HEAD", log);

  if (PUSH_SUCCESS.has(result)) {
    log.info({ path, result }, "pushed project changes");
  } else {
    log.warn({ path, result }, "push failed — changes remain committed locally");
  }
};

/**
 * Pre-finalize post-processor: commits and pushes every dirty registered
 * project before the workspace commit runs, so submodule pointer updates land
 * in the same workspace commit pass.
 */
export const createProjectsProcessor = ({
  workspaceRoot,
  side,
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

    for (const [index, check] of dirtyChecks.entries()) {
      const path = submodulePaths[index] as string;

      if (check.status === "rejected") {
        log.warn({ path, err: check.reason }, "failed to check submodule status");
      } else if (check.value) {
        dirtyPaths.push(path);
      }
    }

    if (dirtyPaths.length === 0) {
      log.debug("no dirty submodules — skipping commit");
      return;
    }

    log.info({ paths: dirtyPaths }, "processing dirty submodules");

    const results = await Promise.allSettled(
      dirtyPaths.map((path) => commitAndPush(workspaceRoot, side, path, log)),
    );

    for (const [index, result] of results.entries()) {
      if (result.status === "rejected") {
        log.warn({ path: dirtyPaths[index], err: result.reason }, "failed to process submodule");
      }
    }
  },
});

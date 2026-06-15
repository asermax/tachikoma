import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import type { RebaseResolver } from "../../git/sync.ts";
import type { Logger } from "../../log.ts";
import type { GitApi } from "../api.ts";
import { checkoutBranch, initSubmodule, listSubmodules, resolveDefaultBranch } from "./git.ts";

const syncSubmodule = async (
  workspaceRoot: string,
  git: GitApi,
  path: string,
  log: Logger,
  resolver?: RebaseResolver,
): Promise<void> => {
  const repoPath = join(workspaceRoot, path);

  await initSubmodule(workspaceRoot, path);

  const defaultBranch = await resolveDefaultBranch(repoPath);
  await checkoutBranch(repoPath, defaultBranch);

  const result = await git.smartPull(repoPath, "origin", defaultBranch, { log, resolver });
  log.info({ path, result }, "submodule synced");
};

const syncSubmoduleWithRetry = async (
  workspaceRoot: string,
  git: GitApi,
  path: string,
  log: Logger,
  resolver?: RebaseResolver,
): Promise<void> => {
  try {
    await syncSubmodule(workspaceRoot, git, path, log, resolver);
  } catch (error) {
    log.debug({ path, err: error }, "submodule sync failed — retrying");
    await syncSubmodule(workspaceRoot, git, path, log, resolver);
  }
};

/**
 * Bootstrap hook: ensure the projects directory exists, then initialize and
 * sync every registered submodule (init → default branch checkout → pull) in
 * parallel with per-submodule error isolation and one retry each. A rebase
 * conflict during pull is handed to the resolver (agent-driven) before falling
 * back to abort; the resolver is cwd-scoped per submodule, so one instance
 * serves them all.
 */
export const syncProjects = async (
  workspaceRoot: string,
  git: GitApi,
  log: Logger,
  resolver?: RebaseResolver,
): Promise<void> => {
  await mkdir(join(workspaceRoot, "projects"), { recursive: true });

  const submodulePaths = await listSubmodules(workspaceRoot);

  if (submodulePaths.length === 0) {
    log.debug("no submodules found — skipping sync");
    return;
  }

  log.info({ count: submodulePaths.length, paths: submodulePaths }, "syncing submodules");

  const results = await Promise.allSettled(
    submodulePaths.map((path) => syncSubmoduleWithRetry(workspaceRoot, git, path, log, resolver)),
  );

  for (const [index, result] of results.entries()) {
    if (result.status === "rejected") {
      log.warn(
        { path: submodulePaths[index], err: result.reason },
        "submodule sync failed after retry",
      );
    }
  }
};

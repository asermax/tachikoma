import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";
import { smartPull } from "../git/sync.ts";
import { checkoutBranch, initSubmodule, listSubmodules, resolveDefaultBranch } from "./git.ts";

const syncSubmodule = async (workspaceRoot: string, path: string, log: Logger): Promise<void> => {
  const repoPath = join(workspaceRoot, path);

  await initSubmodule(workspaceRoot, path);

  const defaultBranch = await resolveDefaultBranch(repoPath);
  await checkoutBranch(repoPath, defaultBranch);

  const result = await smartPull(repoPath, "origin", defaultBranch, log);
  log.info({ path, result }, "submodule synced");
};

const syncSubmoduleWithRetry = async (
  workspaceRoot: string,
  path: string,
  log: Logger,
): Promise<void> => {
  try {
    await syncSubmodule(workspaceRoot, path, log);
  } catch (error) {
    log.debug({ path, err: error }, "submodule sync failed — retrying");
    await syncSubmodule(workspaceRoot, path, log);
  }
};

/**
 * Bootstrap hook: ensure the projects directory exists, then initialize and
 * sync every registered submodule (init → default branch checkout → pull) in
 * parallel with per-submodule error isolation and one retry each.
 */
export const syncProjects = async (workspaceRoot: string, log: Logger): Promise<void> => {
  await mkdir(join(workspaceRoot, "projects"), { recursive: true });

  const submodulePaths = await listSubmodules(workspaceRoot);

  if (submodulePaths.length === 0) {
    log.debug("no submodules found — skipping sync");
    return;
  }

  log.info({ count: submodulePaths.length, paths: submodulePaths }, "syncing submodules");

  const results = await Promise.allSettled(
    submodulePaths.map((path) => syncSubmoduleWithRetry(workspaceRoot, path, log)),
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

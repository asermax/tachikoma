import { rename, rmdir } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { pathExists } from "./fs.ts";

// Legacy workspaces kept these under {workspace}/context/; pi reads
// SOUL.md/USER.md from the workspace root and discovers AGENTS.md natively.
const CONTEXT_FILES = ["SOUL.md", "USER.md", "AGENTS.md"];

export const adaptContextFiles = async (workspace: Workspace, log: Logger): Promise<void> => {
  const oldDir = workspace.resolve("context");

  for (const name of CONTEXT_FILES) {
    const oldPath = join(oldDir, name);
    const newPath = workspace.resolve(name);

    if (!(await pathExists(oldPath))) continue;

    if (await pathExists(newPath)) {
      log.warn(
        { file: name },
        "context file exists in both context/ and the workspace root — keeping the root file, old copy left in context/",
      );
      continue;
    }

    await rename(oldPath, newPath);
    log.info({ file: name }, "moved legacy context file to the workspace root");
  }

  // Tidy up the old directory when nothing is left; rmdir refuses non-empty dirs.
  await rmdir(oldDir).catch((err) => {
    if (err?.code !== "ENOTEMPTY" && err?.code !== "ENOENT") {
      log.debug({ oldDir, err }, "could not remove legacy context dir");
    }
  });
};

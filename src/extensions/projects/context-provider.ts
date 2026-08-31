import type { Logger } from "../../log.ts";
import { describeProjectState, listSubmodules, projectState } from "./git.ts";
import { PROJECTS_USAGE } from "./usage.ts";

/**
 * Memory/projects context: the usage guidance plus a session-start snapshot of each registered
 * submodule (branch or detached commit, uncommitted-change count). Always returns the guidance so
 * the agent knows the project tools exist even when nothing is registered yet.
 */
export const buildProjectsContext = async (workspaceRoot: string, log: Logger): Promise<string> => {
  let submodulePaths: string[];

  try {
    submodulePaths = await listSubmodules(workspaceRoot);
  } catch (error) {
    log.warn({ err: error }, "failed to list submodules");
    submodulePaths = [];
  }

  if (submodulePaths.length === 0) {
    return `${PROJECTS_USAGE}\n\nNo projects are currently registered.`;
  }

  const states = await Promise.allSettled(
    submodulePaths.map((path) => projectState(workspaceRoot, path)),
  );

  const lines = states
    .filter((state) => state.status === "fulfilled")
    .map((state) => describeProjectState(state.value));

  for (const state of states) {
    if (state.status === "rejected") {
      log.warn({ err: state.reason }, "failed to get project info");
    }
  }

  return `${PROJECTS_USAGE}\n\n## Registered Projects\n\n${lines.join("\n")}`;
};

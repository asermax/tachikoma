import type { Logger } from "../../log.ts";
import { describeProjectState, listSubmodules, projectState } from "./git.ts";

const USAGE = `## Projects

You can manage external git repositories alongside your workspace, stored as git submodules under \`projects/\`. They are synced on startup, and any project with uncommitted changes is committed and pushed at session close (before the workspace commit, so submodule pointers land together). Git auth (SSH keys, tokens) is the user's responsibility — if a clone or push fails on auth, point them to configure credentials externally.

The state below is a snapshot from session start — use \`list_projects\` for the live picture.`;

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
    return `${USAGE}\n\nNo projects are currently registered.`;
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

  return `${USAGE}\n\n## Registered Projects\n\n${lines.join("\n")}`;
};

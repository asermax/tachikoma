import type { Logger } from "../../log.ts";
import type { ContextProvider } from "../api.ts";
import { describeProjectState, listSubmodules, projectState } from "./git.ts";

/**
 * Injects awareness of the registered projects: each submodule with its
 * current branch (or detached commit) and uncommitted-change count. Always
 * provides a block so the agent knows register_project is available even when
 * no projects exist yet.
 */
export const createProjectsContextProvider = (
  workspaceRoot: string,
  log: Logger,
): ContextProvider => ({
  name: "projects",

  async provide() {
    let submodulePaths: string[];

    try {
      submodulePaths = await listSubmodules(workspaceRoot);
    } catch (error) {
      log.warn({ err: error }, "failed to list submodules");
      submodulePaths = [];
    }

    if (submodulePaths.length === 0) {
      return {
        tag: "projects",
        content: "No projects registered. Use register_project to add one.",
      };
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

    return { tag: "projects", content: `## Registered Projects\n\n${lines.join("\n")}` };
  },
});

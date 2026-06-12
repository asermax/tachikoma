import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { initializeWorkspaceRepo } from "./hooks.ts";
import { createGitProcessor } from "./processor.ts";
import { createGitToolsFactory } from "./tools.ts";

interface GitConfig {
  enabled: boolean;
}

/**
 * Workspace versioning: initializes the workspace as a git repo on startup
 * (syncing with origin when configured), commits and pushes all workspace
 * changes at session close, and exposes git inspection/commit tools to the
 * agent.
 */
export default defineExtension<GitConfig>({
  name: "git",

  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
  }),

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("git extension disabled by configuration");
      return;
    }

    const workspaceRoot = app.workspace.root;

    app.bootstrap("init-workspace-repo", () => initializeWorkspaceRepo(workspaceRoot, app.log));

    app.agent.use(createGitToolsFactory({ workspaceRoot, side: app.agent.side, log: app.log }));

    app.sessions.registerProcessor(createGitProcessor({ workspaceRoot, side: app.agent.side }));
  },
});

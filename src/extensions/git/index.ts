import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { createGitGuardrailFactory } from "./guardrail.ts";
import { initializeWorkspaceRepo } from "./hooks.ts";
import { createGitProcessor } from "./processor.ts";
import { createGitResolver } from "./resolve.ts";
import { createGitToolsFactory } from "./tools.ts";

interface GitConfig {
  enabled: boolean;
}

/**
 * Workspace versioning: initializes the workspace as a git repo on startup
 * (syncing with origin when configured), commits and pushes all workspace
 * changes at session close, exposes git inspection/commit/scrub tools to the
 * agent, and gates the agent's bash tool against destructive git commands.
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
    const resolver = createGitResolver(app.agent.side);

    app.bootstrap("init-workspace-repo", () =>
      initializeWorkspaceRepo(workspaceRoot, app.log, resolver),
    );

    app.agent.use(createGitToolsFactory({ workspaceRoot, side: app.agent.side, log: app.log }), {
      background: true,
    });
    app.agent.use(createGitGuardrailFactory(app.log), { background: true });

    app.sessions.registerProcessor(
      createGitProcessor({ workspaceRoot, side: app.agent.side, resolver }),
    );
  },
});

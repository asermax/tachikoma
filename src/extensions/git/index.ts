import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { createCommitAgent } from "../../git/commit-agent.ts";
import { createGitResolver } from "../../git/resolve.ts";
import { defineExtension } from "../api.ts";
import { createGitGuardrailFactory } from "./guardrail.ts";
import { initializeWorkspaceRepo } from "./hooks.ts";
import { createGitProcessor } from "./processor.ts";
import { createGitToolsFactory } from "./tools.ts";
import { GIT_USAGE } from "./usage.ts";

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
    const commitAgent = createCommitAgent(app.agent.side, "workspace");

    app.bootstrap("init-workspace-repo", () =>
      initializeWorkspaceRepo(workspaceRoot, app.log, resolver),
    );

    app.agent.use(
      createGitToolsFactory({ workspaceRoot, agent: commitAgent, log: app.log, resolver }),
      { sessionScopes: ["main", "background"] },
    );
    app.agent.use(createGitGuardrailFactory(app.log), { sessionScopes: ["main", "background"] });

    app.agent.use(provideContext(GIT_USAGE, "git-usage"), {
      sessionScopes: ["main", "background"],
    });

    app.sessions.registerProcessor(
      createGitProcessor({ workspaceRoot, agent: commitAgent, resolver }),
    );
  },
});

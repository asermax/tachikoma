import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { createCommitAgent } from "../../git/commit-agent.ts";
import { createGitResolver } from "../../git/resolve.ts";
import { createDebouncedTask } from "../../util/debouncer.ts";
import { defineExtension } from "../api.ts";
import { createGitExchangeProcessor } from "./exchange.ts";
import { createGitGuardrailFactory } from "./guardrail.ts";
import { initializeWorkspaceRepo } from "./hooks.ts";
import { commitAndPushWorkspace, createGitProcessor } from "./processor.ts";
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

    // Debounced per-exchange workspace commit-push: each exchange resets the timer
    // and the workspace is committed and pushed in the background once the
    // configured quiet window elapses. commitDebounceMinutes = 0 disables this
    // (trunk close remains the persistence backstop).
    const debouncer = createDebouncedTask(
      () => commitAndPushWorkspace({ workspaceRoot, agent: commitAgent, resolver, log: app.log }),
      app.config.scheduler.commitDebounceMinutes * 60_000,
      app.log,
    );

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

    // Per-exchange: reset the debounced workspace commit-push timer. The
    // commit-and-push runs in the background after the quiet window; the exchange
    // path only touches the timer.
    app.sessions.onExchange(createGitExchangeProcessor({ debouncer, log: app.log }));

    // At trunk close: the backstop. The processor clears and drains the debouncer,
    // then groups whatever is still uncommitted and pushes once. This close-time
    // agent commit and the parallel per-branch memory extraction both fire at
    // close, so a trunk close drives a burst of side-runner load.
    app.sessions.registerProcessor(
      createGitProcessor({ workspaceRoot, agent: commitAgent, resolver, debouncer }),
    );

    // Cancel any pending debounce fire on shutdown — the drain's finalize pass
    // handles persistence.
    app.onShutdown("git-debounce", () => debouncer.clear());
  },
});

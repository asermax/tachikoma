import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { createGitResolver } from "../../git/resolve.ts";
import { createDebouncedTask } from "../../util/debouncer.ts";
import { defineExtension } from "../api.ts";
import { buildProjectsContext } from "./context-provider.ts";
import { syncProjects } from "./hooks.ts";
import {
  commitAndPushSubmodules,
  createProjectsExchangeProcessor,
  createProjectsProcessor,
} from "./processor.ts";
import { createProjectsToolsFactory } from "./tools.ts";

interface ProjectsConfig {
  enabled: boolean;
}

/**
 * External project management: registers git repositories as submodules under
 * projects/, syncs them on startup, surfaces their git state on every message,
 * and commits + pushes dirty projects at session close (before the workspace
 * commit, so submodule pointer updates land in the same pass).
 */
export default defineExtension<ProjectsConfig>({
  name: "projects",

  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
  }),

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("projects extension disabled by configuration");
      return;
    }

    const workspaceRoot = app.workspace.root;
    // One cwd-scoped resolver serves every submodule — it captures the repo path
    // from each smartPull/smartPush call, so it can act inside any project tree.
    const resolver = createGitResolver(app.agent.side);
    const projectAgent = app.git.createCommitAgent("project");

    // Debounced per-exchange projects commit-push: each exchange resets the timer
    // and every dirty registered project is committed and pushed in the background
    // once the configured quiet window elapses. commitDebounceMinutes = 0 disables
    // this (trunk close remains the persistence backstop).
    const debouncer = createDebouncedTask(
      () =>
        commitAndPushSubmodules({
          workspaceRoot,
          git: app.git,
          agent: projectAgent,
          resolver,
          timezone: app.config.scheduler.timezone,
          log: app.log,
        }),
      app.config.scheduler.commitDebounceMinutes * 60_000,
      app.log,
    );

    app.bootstrap("sync-projects", () => syncProjects(workspaceRoot, app.git, app.log, resolver));

    app.agent.use(createProjectsToolsFactory({ workspaceRoot, log: app.log }), {
      sessionScopes: ["main", "background"],
    });
    app.agent.use(
      provideContext(() => buildProjectsContext(workspaceRoot, app.log), "projects"),
      {
        sessionScopes: ["main", "background"],
      },
    );

    // Per-exchange: reset the debounced projects commit-push timer. The
    // commit-and-push runs in the background after the quiet window; the exchange
    // path only touches the timer.
    app.sessions.onExchange(createProjectsExchangeProcessor({ debouncer, log: app.log }));

    // At trunk close: the backstop (pre-finalize, before the workspace commit so
    // submodule pointer updates land in the same pass). Clears and drains the
    // debouncer, then commits + pushes dirty projects and any clean-ahead ones.
    app.sessions.registerProcessor(
      createProjectsProcessor({
        workspaceRoot,
        agent: projectAgent,
        git: app.git,
        resolver,
        debouncer,
        timezone: app.config.scheduler.timezone,
      }),
    );

    // Cancel any pending debounce fire on shutdown — the drain's finalize pass
    // handles persistence.
    app.onShutdown("projects-debounce", () => debouncer.clear());
  },
});

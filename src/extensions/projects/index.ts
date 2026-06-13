import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { createProjectsContextProvider } from "./context-provider.ts";
import { syncProjects } from "./hooks.ts";
import { createProjectsProcessor } from "./processor.ts";
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

    app.bootstrap("sync-projects", () => syncProjects(workspaceRoot, app.log));

    app.agent.provideContext(createProjectsContextProvider(workspaceRoot, app.log));
    app.agent.use(createProjectsToolsFactory({ workspaceRoot, log: app.log }), {
      background: true,
    });

    app.sessions.registerProcessor(
      createProjectsProcessor({ workspaceRoot, side: app.agent.side }),
    );
  },
});

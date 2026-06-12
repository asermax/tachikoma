import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { createStaleWorkflowCleanup, DEFAULT_STALE_HOURS } from "./cleanup.ts";
import { findWorkflow } from "./loader.ts";
import { WorkflowStateRepository } from "./repository.ts";
import { registerWorkflowTools } from "./tools.ts";

interface WorkflowsConfig {
  enabled: boolean;
  staleHours: number;
}

/**
 * Workflow engine: directory-based workflow definitions inside skills execute as
 * database-persisted step state machines, driven by the agent through lifecycle
 * tools. Stale instances are expired at session close.
 */
export default defineExtension<WorkflowsConfig>({
  name: "workflows",

  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
    staleHours: Type.Number({ default: DEFAULT_STALE_HOURS }),
  }),

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("workflows disabled by configuration");
      return;
    }

    const repository = new WorkflowStateRepository(app.db);
    const skillsDir = app.workspace.resolve("skills");
    const scratchpadDir = join(app.workspace.dataDir, "scratchpads");

    app.bootstrap("ensure-scratchpad-dir", async () => {
      await mkdir(scratchpadDir, { recursive: true });
    });

    app.agent.use((pi) => {
      registerWorkflowTools(pi, {
        repository,
        findWorkflow: (skillName, workflowName) =>
          findWorkflow(skillsDir, skillName, workflowName, app.log),
        scratchpadDir,
        log: app.log,
      });
    });

    app.sessions.registerProcessor(
      createStaleWorkflowCleanup(repository, app.extensionConfig.staleHours),
    );
  },
});

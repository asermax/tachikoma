import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { SystemctlScopeInspector } from "./cgroup.ts";
import { SystemdRunLimiter } from "./limits.ts";
import {
  type ProcessNotification,
  type ReconcileDeps,
  reconcileExit,
  reconcileOnStartup,
} from "./reconcile.ts";
import { ProcessRepository } from "./repository.ts";
import { createProcessToolsFactory } from "./tools.ts";
import { DETACHED_PROCESSES_USAGE } from "./usage.ts";
import { createWatcherTick } from "./watcher.ts";

interface DetachedProcessesConfig {
  /** Default memory limit applied to spawned processes; 0 disables the default. */
  defaultMemoryLimitMb: number;
  watchIntervalSeconds: number;
}

/**
 * Detached processes: spawn shell commands that outlive Tachikoma, capture
 * their output to files, watch for exits, and notify on completion. The agent
 * manages them through the process tools.
 */
export default defineExtension<DetachedProcessesConfig>({
  name: "detached-processes",

  configSchema: Type.Object({
    defaultMemoryLimitMb: Type.Number({ default: 1024 }),
    watchIntervalSeconds: Type.Number({ default: 15 }),
  }),

  setup(app) {
    const repository = new ProcessRepository(app.db);
    const limiter = new SystemdRunLimiter(app.log);
    const scopeInspector = new SystemctlScopeInspector(app.log);
    const processesDir = join(app.workspace.dataDir, "processes");

    const reconcile: ReconcileDeps = {
      repository,
      processesDir,
      notify: (notification: ProcessNotification) =>
        app.events.emit("notify", {
          title: `Process ${notification.processId}`,
          text: notification.message,
          severity: notification.severity,
          source: notification.source,
        }),
      scopeInspector,
      log: app.log,
    };

    app.bootstrap("reconcile", async () => {
      await mkdir(processesDir, { recursive: true });
      await limiter.detect();
      await reconcileOnStartup(reconcile);
    });

    app.agent.use(
      createProcessToolsFactory({
        ...reconcile,
        limiter,
        // Reconcile the moment a child exits rather than waiting for the next sweep.
        onExit: (id: string) => void reconcileExit(reconcile, id),
        defaultMemoryLimitMb:
          app.extensionConfig.defaultMemoryLimitMb > 0
            ? app.extensionConfig.defaultMemoryLimitMb
            : null,
      }),
      { sessionScopes: ["main", "background"] },
    );

    app.agent.use({
      name: "detached-processes-usage",
      contextProvider: DETACHED_PROCESSES_USAGE,
      sessionScopes: ["main", "background"],
    });

    app.scheduler.every(
      "detached-watch",
      app.extensionConfig.watchIntervalSeconds,
      createWatcherTick(reconcile),
    );
  },
});

import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { SystemdRunLimiter } from "./limits.ts";
import { type ProcessNotification, type ReconcileDeps, reconcileOnStartup } from "./reconcile.ts";
import { ProcessRepository } from "./repository.ts";
import { createProcessToolsFactory } from "./tools.ts";
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
    const processesDir = join(app.workspace.dataDir, "processes");

    const reconcile: ReconcileDeps = {
      repository,
      processesDir,
      notify: (notification: ProcessNotification) =>
        app.events.emit("notify", {
          title: `Process ${notification.processId}`,
          text: notification.message,
          severity: notification.severity === "error" ? "warning" : "info",
          source: notification.source,
        }),
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
        defaultMemoryLimitMb:
          app.extensionConfig.defaultMemoryLimitMb > 0
            ? app.extensionConfig.defaultMemoryLimitMb
            : null,
      }),
    );

    app.scheduler.every(
      "detached-watch",
      app.extensionConfig.watchIntervalSeconds,
      createWatcherTick(reconcile),
    );
  },
});

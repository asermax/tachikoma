import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { BackgroundRunner } from "./executor.ts";
import { expireWaitingInstances } from "./expiration.ts";
import { generateDueInstances } from "./generation.ts";
import { TaskRepository } from "./repository.ts";
import { deliverSessionTasks } from "./session-delivery.ts";
import { createTaskToolsFactory } from "./tools.ts";

interface TasksConfig {
  timezone?: string;
  sessionTaskMaxHoldSeconds: number;
  backgroundMaxIterations: number;
  waitTimeoutSeconds: number;
}

const TICK_INTERVAL_SECONDS = 60;

/**
 * Scheduled tasks: cron or one-shot definitions fire as instances that are
 * either delivered into the conversation during idle time (session mode) or
 * executed autonomously through an evaluator loop (background mode). The agent
 * manages definitions through the task tools.
 */
export default defineExtension<TasksConfig>({
  name: "tasks",

  configSchema: Type.Object({
    timezone: Type.Optional(Type.String()),
    sessionTaskMaxHoldSeconds: Type.Number({ default: 900 }),
    backgroundMaxIterations: Type.Number({ default: 10 }),
    waitTimeoutSeconds: Type.Number({ default: 7200 }),
  }),

  setup(app) {
    const repository = new TaskRepository(app.db);
    const timezone = app.extensionConfig.timezone ?? app.config.scheduler.timezone;
    const now = () => new Date();

    app.bootstrap("crash-recovery", () => {
      const count = repository.markRunningAsFailed("system restart");

      if (count > 0) {
        app.log.warn({ count }, "crash recovery: marked running instances as failed");
      }
    });

    const runner = new BackgroundRunner({
      repository,
      side: app.agent.side,
      deliver: (delivery) => app.channels.deliver(delivery),
      // User-facing output goes through deliver(); this signal is for other extensions.
      notify: (notification) => app.events.emit("tasks:instance-finished", notification),
      maxIterations: app.extensionConfig.backgroundMaxIterations,
      timezone,
      now,
      log: app.log,
    });

    app.agent.use(createTaskToolsFactory({ repository, timezone, now }));

    app.scheduler.every("tasks-tick", TICK_INTERVAL_SECONDS, () => {
      generateDueInstances({ repository, timezone, now, log: app.log });

      expireWaitingInstances({
        repository,
        waitTimeoutSeconds: app.extensionConfig.waitTimeoutSeconds,
        now,
        log: app.log,
        onExpired: (instance, reason) => {
          app.channels.deliver({ text: `❌ Background task failed: ${reason}`, gate: "idle" });
          app.events.emit("tasks:instance-finished", {
            source: "Background task",
            instanceId: instance.id,
            status: "failed",
            message: reason,
          });
        },
      });

      deliverSessionTasks({
        repository,
        deliver: (delivery) => app.channels.deliver(delivery),
        maxHoldSeconds: app.extensionConfig.sessionTaskMaxHoldSeconds,
        now,
        log: app.log,
      });

      // Background executions run detached — the runner tracks them across ticks.
      runner.tick();
    });
  },
});

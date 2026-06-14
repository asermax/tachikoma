import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { BackgroundRunner } from "./executor.ts";
import { expireWaitingInstances } from "./expiration.ts";
import { generateDueInstances } from "./generation.ts";
import { cleanupExpiredOneShots } from "./one-shot-cleanup.ts";
import { TaskRepository } from "./repository.ts";
import { deliverSessionTasks } from "./session-delivery.ts";
import { failStuckRunningInstances } from "./stuck-running.ts";
import { createTaskInteractiveToolsFactory, createTaskToolsFactory } from "./tools.ts";
import { buildTasksUsage } from "./usage.ts";

interface TasksConfig {
  timezone?: string;
  backgroundMaxIterations: number;
  backgroundMaxConcurrent: number;
  waitTimeoutSeconds: number;
  runningTimeoutSeconds: number;
  oneShotRetentionSeconds: number;
}

const TICK_INTERVAL_SECONDS = 60;

const CLEANUP_INTERVAL_SECONDS = 3600;

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
    backgroundMaxIterations: Type.Number({ default: 10 }),
    // Cap on concurrent background runs — surplus pending instances wait for a
    // free slot on a later tick, bounding the side-run / API burst they cause.
    backgroundMaxConcurrent: Type.Number({ default: 3 }),
    waitTimeoutSeconds: Type.Number({ default: 7200 }),
    // A background run held in `running` longer than this is presumed dead or
    // wedged; the stuck-running sweep fails it to free its concurrency slot.
    runningTimeoutSeconds: Type.Number({ default: 1800 }),
    // Retention window after which auto-disabled one-shot definitions (and their
    // terminal instances) are pruned so spent one-shots don't accumulate.
    oneShotRetentionSeconds: Type.Number({ default: 172800 }),
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
      runPostProcessors: (context) => app.sessions.runPostProcessors(context),
      maxIterations: app.extensionConfig.backgroundMaxIterations,
      maxConcurrent: app.extensionConfig.backgroundMaxConcurrent,
      timezone,
      now,
      log: app.log,
    });

    app.agent.use(createTaskToolsFactory({ repository, timezone, now }), {
      sessionScopes: ["main", "background"],
    });

    app.agent.use({
      name: "tasks-usage",
      contextProvider: buildTasksUsage(timezone),
      sessionScopes: ["main", "background"],
    });

    // respond_to_task is conversational-only: a background run must never answer
    // another instance's waiting question, so it stays out of background toolsets.
    app.agent.use(createTaskInteractiveToolsFactory({ repository, timezone, now }));

    app.scheduler.every("tasks-tick", TICK_INTERVAL_SECONDS, () => {
      generateDueInstances({ repository, timezone, now, log: app.log });

      expireWaitingInstances({
        repository,
        waitTimeoutSeconds: app.extensionConfig.waitTimeoutSeconds,
        now,
        log: app.log,
        onExpired: (instance, reason) => {
          app.channels.deliver({ text: `❌ Background task failed: ${reason}`, tier: "normal" });
          app.events.emit("tasks:instance-finished", {
            source: "Background task",
            instanceId: instance.id,
            status: "failed",
            message: reason,
          });
        },
      });

      failStuckRunningInstances({
        repository,
        runningTimeoutSeconds: app.extensionConfig.runningTimeoutSeconds,
        now,
        log: app.log,
        onStuck: (instance, reason) => {
          app.channels.deliver({ text: `❌ Background task failed: ${reason}`, tier: "normal" });
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
        now,
        log: app.log,
      });

      // Background executions run detached — the runner tracks them across ticks.
      runner.tick();
    });

    // Retention pruning is low-churn — it runs on a slower cadence than the
    // per-minute tick so it never competes with generation/delivery work.
    app.scheduler.every("tasks-one-shot-cleanup", CLEANUP_INTERVAL_SECONDS, () => {
      cleanupExpiredOneShots({
        repository,
        retentionSeconds: app.extensionConfig.oneShotRetentionSeconds,
        log: app.log,
      });
    });
  },
});

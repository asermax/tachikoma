import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { DISPATCH_BACKGROUND_TASK_EVENT, NOTIFY_EVENT, SEVERITIES } from "../../events.ts";
import { defineExtension } from "../api.ts";
import { handleDispatchEvent } from "./dispatch.ts";
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
 * executed autonomously through a goal-driven self-declaration loop (background mode). The agent
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

    // Ad-hoc background instances gain a programmatic creator (DLT-080's skill-evolution
    // reporter is the first emitter) — the subscriber lives in dispatch.ts.
    app.events.on(DISPATCH_BACKGROUND_TASK_EVENT, (payload) =>
      handleDispatchEvent({ repository, now, log: app.log }, payload),
    );

    app.bootstrap("crash-recovery", () => {
      const count = repository.markRunningAsFailed("system restart");

      if (count > 0) {
        app.log.warn({ count }, "crash recovery: marked running instances as failed");
      }
    });

    const runner = new BackgroundRunner({
      repository,
      side: app.agent.side,
      // Background-task notices flow through the notifications router via the "notify" event.
      emit: (event, payload) => app.events.emit(event, payload),
      runPostProcessors: (context) => app.sessions.runPostProcessors(context),
      maxIterations: app.extensionConfig.backgroundMaxIterations,
      maxConcurrent: app.extensionConfig.backgroundMaxConcurrent,
      timezone,
      now,
      log: app.log,
    });

    app.agent.use(
      createTaskToolsFactory({
        repository,
        timezone,
        now,
        cancelRunningInstance: (id) => runner.cancel(id),
        log: app.log,
      }),
      {
        sessionScopes: ["main", "background"],
      },
    );

    app.agent.use(provideContext(buildTasksUsage(timezone), "tasks-usage"), {
      sessionScopes: ["main", "background"],
    });

    // respond_to_task is conversational-only: a background run must never answer
    // another instance's waiting question, so it stays out of background toolsets.
    app.agent.use(createTaskInteractiveToolsFactory({ repository, timezone, now, log: app.log }));

    app.scheduler.every("tasks-tick", TICK_INTERVAL_SECONDS, () => {
      generateDueInstances({ repository, timezone, now, log: app.log });

      expireWaitingInstances({
        repository,
        waitTimeoutSeconds: app.extensionConfig.waitTimeoutSeconds,
        now,
        log: app.log,
        onExpired: (_instance, reason) => {
          app.events.emit(NOTIFY_EVENT, {
            text: `❌ ${reason}`,
            severity: SEVERITIES.warning,
            source: "Background task",
          });
        },
      });

      failStuckRunningInstances({
        repository,
        runningTimeoutSeconds: app.extensionConfig.runningTimeoutSeconds,
        now,
        log: app.log,
        onStuck: (_instance, reason) => {
          app.events.emit(NOTIFY_EVENT, {
            text: `❌ ${reason}`,
            severity: SEVERITIES.warning,
            source: "Background task",
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

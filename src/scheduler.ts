import { Cron } from "croner";

import type { Logger } from "./log.ts";

export interface ScheduledJob {
  name: string;
  stop(): void;
  trigger(): Promise<void>;
}

export class Scheduler {
  private readonly jobs = new Map<string, ScheduledJob>();
  private readonly log: Logger;
  private readonly timezone?: string;

  constructor(log: Logger, timezone?: string) {
    this.log = log;
    this.timezone = timezone;
  }

  cron(name: string, pattern: string, fn: () => void | Promise<void>): ScheduledJob {
    const job = new Cron(
      pattern,
      { timezone: this.timezone, protect: true, catch: this.onError(name) },
      this.ticking(name, fn),
    );

    const handle: ScheduledJob = {
      name,
      stop: () => job.stop(),
      // Manual triggers bypass overrun protection: an explicit trigger must never be silently skipped.
      trigger: this.guarded(name, fn),
    };

    this.register(handle);
    return handle;
  }

  every(name: string, seconds: number, fn: () => void | Promise<void>): ScheduledJob {
    // A wildcard pattern with `interval` fires roughly every `seconds`; `protect`
    // is croner's native single-flight, skipping a tick while the previous run is active.
    const job = new Cron(
      "* * * * * *",
      {
        timezone: this.timezone,
        interval: seconds,
        protect: () =>
          this.log.debug({ job: name }, "scheduled job skipped — previous run still active"),
        catch: this.onError(name),
        unref: true,
      },
      this.ticking(name, fn),
    );

    const handle: ScheduledJob = {
      name,
      stop: () => job.stop(),
      // Manual triggers bypass overrun protection: an explicit trigger must never be silently skipped.
      trigger: this.guarded(name, fn),
    };

    this.register(handle);
    return handle;
  }

  stopAll(): void {
    for (const job of this.jobs.values()) job.stop();
    this.jobs.clear();
  }

  private register(job: ScheduledJob): void {
    this.jobs.get(job.name)?.stop();
    this.jobs.set(job.name, job);
  }

  private onError(name: string): (error: unknown) => void {
    return (error) => this.log.error({ job: name, err: error }, "scheduled job failed");
  }

  // Logs each scheduled tick's start and successful completion (with duration) at debug; a throwing
  // tick rejects to croner's `catch`, which routes to onError — so no finish line on failure.
  private ticking(name: string, fn: () => void | Promise<void>): () => Promise<void> {
    return async () => {
      this.log.debug({ job: name }, "scheduled job fired");

      const startedAt = Date.now();

      await fn();

      this.log.debug({ job: name, durationMs: Date.now() - startedAt }, "scheduled job finished");
    };
  }

  // Manual-trigger wrapper: errors are logged here since this path bypasses
  // croner's scheduled execution (and therefore its `catch` handler).
  private guarded(name: string, fn: () => void | Promise<void>): () => Promise<void> {
    return async () => {
      try {
        await fn();
      } catch (error) {
        this.onError(name)(error);
      }
    };
  }
}

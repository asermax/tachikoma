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
    const run = this.guarded(name, fn);

    const job = new Cron(pattern, { timezone: this.timezone, protect: true, catch: true }, run);
    const handle: ScheduledJob = {
      name,
      stop: () => job.stop(),
      trigger: run,
    };

    this.register(handle);
    return handle;
  }

  every(name: string, seconds: number, fn: () => void | Promise<void>): ScheduledJob {
    const guarded = this.guarded(name, fn);
    const run = this.singleFlight(name, guarded);

    const interval = setInterval(run, seconds * 1000);
    interval.unref();

    const handle: ScheduledJob = {
      name,
      stop: () => clearInterval(interval),
      // Manual triggers bypass single-flight: an explicit trigger must never be silently skipped.
      trigger: guarded,
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

  private guarded(name: string, fn: () => void | Promise<void>): () => Promise<void> {
    return async () => {
      try {
        await fn();
      } catch (error) {
        this.log.error({ job: name, err: error }, "scheduled job failed");
      }
    };
  }

  private singleFlight(name: string, fn: () => void | Promise<void>): () => Promise<void> {
    let running = false;

    return async () => {
      if (running) {
        this.log.debug({ job: name }, "scheduled job skipped — previous run still active");
        return;
      }

      running = true;

      try {
        await fn();
      } finally {
        running = false;
      }
    };
  }
}

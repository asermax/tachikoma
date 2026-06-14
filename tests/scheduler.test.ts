import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Logger } from "../src/log.ts";
import { Scheduler } from "../src/scheduler.ts";

const createLogger = () =>
  ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) as unknown as Logger;

describe("Scheduler.every", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not overlap a slow job, then resumes once it settles", async () => {
    const scheduler = new Scheduler(createLogger());

    let release!: () => void;
    const fn = vi.fn(() => new Promise<void>((resolve) => (release = resolve)));

    scheduler.every("slow", 1, fn);

    await vi.advanceTimersByTimeAsync(3000);
    expect(fn).toHaveBeenCalledTimes(1);

    release();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fn).toHaveBeenCalledTimes(2);

    scheduler.stopAll();
  });

  it("keeps firing after a thrown error", async () => {
    const scheduler = new Scheduler(createLogger());

    const fn = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(undefined);

    scheduler.every("flaky", 1, fn);

    await vi.advanceTimersByTimeAsync(1000);
    expect(fn).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(fn).toHaveBeenCalledTimes(2);

    scheduler.stopAll();
  });

  it("replaces a prior job when re-registering the same name", async () => {
    const scheduler = new Scheduler(createLogger());

    const first = vi.fn();
    const second = vi.fn();

    scheduler.every("dup", 1, first);
    scheduler.every("dup", 1, second);

    await vi.advanceTimersByTimeAsync(1000);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);

    scheduler.stopAll();
  });

  it("stopAll cancels interval jobs", async () => {
    const scheduler = new Scheduler(createLogger());

    const fn = vi.fn();
    scheduler.every("stoppable", 1, fn);

    scheduler.stopAll();

    await vi.advanceTimersByTimeAsync(5000);
    expect(fn).not.toHaveBeenCalled();
  });

  it("runs the manual trigger even while a run is still active", async () => {
    const scheduler = new Scheduler(createLogger());

    let release!: () => void;
    const fn = vi.fn(() => new Promise<void>((resolve) => (release = resolve)));

    const job = scheduler.every("triggerable", 1, fn);

    await vi.advanceTimersByTimeAsync(1000);
    expect(fn).toHaveBeenCalledTimes(1);

    void job.trigger();
    expect(fn).toHaveBeenCalledTimes(2);

    release();
    scheduler.stopAll();
  });

  it("logs an error when a manual trigger throws", async () => {
    const log = createLogger();
    const scheduler = new Scheduler(log);

    const fn = vi.fn().mockRejectedValue(new Error("trigger boom"));
    const job = scheduler.every("failing-trigger", 1, fn);

    await job.trigger();

    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ job: "failing-trigger" }),
      "scheduled job failed",
    );

    scheduler.stopAll();
  });
});

describe("Scheduler.cron", () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: new Date("2026-06-14T12:00:00Z") });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires on its cron pattern", async () => {
    const scheduler = new Scheduler(createLogger(), "UTC");

    const fn = vi.fn();
    scheduler.cron("minutely", "* * * * *", fn);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(fn).toHaveBeenCalledTimes(1);

    scheduler.stopAll();
  });

  it("runs the manual trigger immediately, bypassing the schedule", async () => {
    const scheduler = new Scheduler(createLogger(), "UTC");

    const fn = vi.fn();
    const job = scheduler.cron("hourly", "0 * * * *", fn);

    await job.trigger();
    expect(fn).toHaveBeenCalledTimes(1);

    scheduler.stopAll();
  });

  it("replaces a prior cron job when re-registering the same name", async () => {
    const scheduler = new Scheduler(createLogger(), "UTC");

    const first = vi.fn();
    const second = vi.fn();

    scheduler.cron("dup-cron", "* * * * *", first);
    scheduler.cron("dup-cron", "* * * * *", second);

    await vi.advanceTimersByTimeAsync(60_000);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);

    scheduler.stopAll();
  });

  it("logs scheduled cron failures via the catch handler", async () => {
    const log = createLogger();
    const scheduler = new Scheduler(log, "UTC");

    const fn = vi.fn().mockRejectedValue(new Error("cron boom"));
    scheduler.cron("flaky-cron", "* * * * *", fn);

    await vi.advanceTimersByTimeAsync(60_000);
    await vi.advanceTimersByTimeAsync(0);

    expect(fn).toHaveBeenCalled();
    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ job: "flaky-cron" }),
      "scheduled job failed",
    );

    scheduler.stopAll();
  });
});

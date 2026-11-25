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

  // croner 11 pins (S5). New York has DST. On 2026-11-01 its clock falls back from 02:00 to 01:00
  // at 06:00Z, so the wall clock repeats the hour from 01:00 to 01:59.
  it("keeps its elapsed interval through a DST fall-back overlap", async () => {
    vi.useFakeTimers({ now: new Date("2026-11-01T05:59:00Z") });
    const scheduler = new Scheduler(createLogger(), "America/New_York");

    const firedAt: number[] = [];
    scheduler.every("overlap", 1800, () => {
      firedAt.push(Date.now());
    });

    // Three hours of elapsed time, across the repeated hour.
    await vi.advanceTimersByTimeAsync(3 * 3_600_000);

    expect(firedAt).toHaveLength(6);
    const gaps = firedAt.slice(1).map((at, index) => at - (firedAt[index] ?? 0));
    expect(gaps).toEqual([1_800_000, 1_800_000, 1_800_000, 1_800_000, 1_800_000]);

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

  // croner 11 pins (S5), in a timezone with DST.
  it("runs a fixed-time job once, at the first valid time after a DST gap", async () => {
    // 01:59 in New York, one minute before the clock jumps from 02:00 to 03:00 on 2026-03-08.
    vi.useFakeTimers({ now: new Date("2026-03-08T06:59:00Z") });
    const scheduler = new Scheduler(createLogger(), "America/New_York");

    const firedAt: Date[] = [];
    scheduler.cron("gap", "30 2 * * *", () => {
      firedAt.push(new Date());
    });

    // 02:30 does not exist on this day. The job waits for 03:00, the end of the gap, where
    // croner 10 ran it at 03:30.
    await vi.advanceTimersByTimeAsync(59_000);
    expect(firedAt).toEqual([]);

    await vi.advanceTimersByTimeAsync(2000);
    expect(firedAt.map((date) => date.toISOString())).toEqual(["2026-03-08T07:00:00.000Z"]);

    // Nothing at 03:30, and nothing until tomorrow's 02:30.
    await vi.advanceTimersByTimeAsync(3_600_000);
    expect(firedAt).toHaveLength(1);

    scheduler.stopAll();
  });

  it("does not fire an interval job whose times are inside a DST gap", async () => {
    // 01:59 in New York, one minute before the clock jumps from 02:00 to 03:00 on 2026-03-08.
    const start = new Date("2026-03-08T06:59:00Z");
    vi.useFakeTimers({ now: start });
    const scheduler = new Scheduler(createLogger(), "America/New_York");

    const inGap = vi.fn();
    const firedAt: Date[] = [];
    scheduler.cron("in-gap", "*/30 2 * * *", inGap);
    scheduler.cron("after-gap", "*/30 3 * * *", () => {
      firedAt.push(new Date());
    });

    // Through 04:00:01 local, and not to the end of the day. croner re-arms a timer every 30
    // seconds, and the next run of the job in the gap is 02:00 the following day, which
    // tests/tasks/schedule.test.ts pins.
    await vi.advanceTimersByTimeAsync(Date.parse("2026-03-08T08:00:01Z") - start.getTime());

    // 02:00 and 02:30 do not exist on this day. croner 11 skips them, and Tachikoma adopts that as
    // it is; tests/tasks/schedule.test.ts says why. croner 10.0.1 fired the job at 03:00. The job
    // on the next hour fires at 03:00 and 03:30, so the scheduler was running all along.
    expect(inGap).not.toHaveBeenCalled();
    expect(firedAt.map((date) => date.toISOString())).toEqual([
      "2026-03-08T07:00:00.000Z",
      "2026-03-08T07:30:00.000Z",
    ]);

    scheduler.stopAll();
  });

  it("runs an hourly job on the second occurrence of a repeated hour in a DST fall-back overlap", async () => {
    // 00:30 in New York on 2026-11-01. The clock falls back from 02:00 to 01:00 at 06:00Z, so
    // 01:00 comes at 05:00Z and again at 06:00Z. croner 10 skipped the second one.
    const start = new Date("2026-11-01T05:30:00Z");
    vi.useFakeTimers({ now: start });
    const scheduler = new Scheduler(createLogger(), "America/New_York");

    const firedAt: Date[] = [];
    scheduler.cron("hourly", "0 * * * *", () => {
      firedAt.push(new Date());
    });

    // Just past the third expected run.
    await vi.advanceTimersByTimeAsync(Date.parse("2026-11-01T08:00:01Z") - start.getTime());

    expect(firedAt.map((date) => date.toISOString())).toEqual([
      "2026-11-01T06:00:00.000Z",
      "2026-11-01T07:00:00.000Z",
      "2026-11-01T08:00:00.000Z",
    ]);

    scheduler.stopAll();
  });
});

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
});

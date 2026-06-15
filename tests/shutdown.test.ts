import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Logger } from "../src/log.ts";
import { ShutdownController } from "../src/shutdown.ts";

const createFakeLog = () => {
  const log = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

describe("ShutdownController", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("aborts on a graceful signal without crashing or arming a timer (AC4)", () => {
    const abort = new AbortController();
    const exit = vi.fn();
    const log = createFakeLog();
    const controller = new ShutdownController({ abort, log, exit });

    controller.trigger("SIGINT");

    expect(abort.signal.aborted).toBe(true);
    expect(controller.didCrash).toBe(false);
    expect(log.info).toHaveBeenCalledWith(
      expect.objectContaining({ cause: "SIGINT" }),
      "shutting down",
    );
    expect(exit).not.toHaveBeenCalled();

    vi.runOnlyPendingTimers(); // nothing armed on the signal path
    expect(exit).not.toHaveBeenCalled();
  });

  it("drains on an uncaught exception: aborts, marks crashed, logs, and arms a force-exit timer (AC1/AC3)", () => {
    const abort = new AbortController();
    const exit = vi.fn();
    const log = createFakeLog();
    const controller = new ShutdownController({ abort, log, exit });

    const err = new Error("boom");
    controller.trigger("uncaughtException", err);

    expect(abort.signal.aborted).toBe(true);
    expect(controller.didCrash).toBe(true);
    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ cause: "uncaughtException", err }),
      "unrecoverable error — draining before exit",
    );
    expect(exit).not.toHaveBeenCalled(); // timer armed but not yet fired

    vi.advanceTimersByTime(180_000);
    expect(exit).toHaveBeenCalledWith(1);
  });

  it("treats an unhandled rejection as a crash cause", () => {
    const abort = new AbortController();
    const controller = new ShutdownController({ abort, log: createFakeLog(), exit: vi.fn() });

    controller.trigger("unhandledRejection", "reason");

    expect(controller.didCrash).toBe(true);
    expect(abort.signal.aborted).toBe(true);
  });

  it("force-exits immediately on a second trigger and clears the armed timer (AC2)", () => {
    const abort = new AbortController();
    const exit = vi.fn();
    const controller = new ShutdownController({ abort, log: createFakeLog(), exit });

    controller.trigger("uncaughtException"); // arms the timer
    controller.trigger("SIGINT"); // second trigger → immediate force-exit

    expect(exit).toHaveBeenCalledWith(1);

    vi.advanceTimersByTime(180_000); // timer was cleared → must not fire again
    expect(exit).toHaveBeenCalledTimes(1);
  });

  it("is idempotent: a second graceful trigger force-exits rather than re-aborting", () => {
    const abort = new AbortController();
    const exit = vi.fn();
    const controller = new ShutdownController({ abort, log: createFakeLog(), exit });

    controller.trigger("SIGTERM");
    controller.trigger("SIGTERM");

    expect(abort.signal.aborted).toBe(true); // aborted exactly once
    expect(exit).toHaveBeenCalledWith(1); // second trigger force-exited
  });
});

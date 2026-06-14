import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExchangeProcessor } from "../../src/extensions/api.ts";
import { type IdleSessions, registerIdleClose } from "../../src/extensions/boundary/idle.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { info: vi.fn(), error: vi.fn() } as unknown as Logger;

const setup = (idleCloseSeconds: number, closeIfIdle = vi.fn().mockResolvedValue(true)) => {
  let processor: ExchangeProcessor | null = null;

  const sessions: IdleSessions = {
    onExchange: (registered) => {
      processor = registered;
    },
    closeIfIdle,
  };

  registerIdleClose(sessions, idleCloseSeconds, fakeLog);

  const exchange = () =>
    (processor as ExchangeProcessor | null)?.process({} as never) ?? Promise.resolve();

  return { closeIfIdle, exchange };
};

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("idle close boundary", () => {
  it("closes the session after the silence window", async () => {
    const { closeIfIdle, exchange } = setup(900);

    await exchange();
    await vi.advanceTimersByTimeAsync(900_000);

    expect(closeIfIdle).toHaveBeenCalledTimes(1);
  });

  it("re-arms on every exchange instead of stacking timers", async () => {
    const { closeIfIdle, exchange } = setup(900);

    await exchange();
    await vi.advanceTimersByTimeAsync(800_000);
    await exchange();
    await vi.advanceTimersByTimeAsync(800_000);

    expect(closeIfIdle).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(100_000);
    expect(closeIfIdle).toHaveBeenCalledTimes(1);
  });

  it("does not fire before any exchange happened", async () => {
    const { closeIfIdle } = setup(900);

    await vi.advanceTimersByTimeAsync(3_600_000);

    expect(closeIfIdle).not.toHaveBeenCalled();
  });

  it("logs the close when a session was actually closed", async () => {
    const { exchange } = setup(900, vi.fn().mockResolvedValue(true));

    await exchange();
    await vi.advanceTimersByTimeAsync(900_000);

    expect(fakeLog.info).toHaveBeenCalledWith(
      { idleCloseSeconds: 900 },
      "idle timeout reached — session closed",
    );
  });

  it("does not log when no session was idle", async () => {
    vi.mocked(fakeLog.info).mockClear();
    const { exchange } = setup(900, vi.fn().mockResolvedValue(false));

    await exchange();
    await vi.advanceTimersByTimeAsync(900_000);

    expect(fakeLog.info).not.toHaveBeenCalled();
  });

  it("logs an error when the close attempt rejects", async () => {
    const error = new Error("close failed");
    const { exchange } = setup(900, vi.fn().mockRejectedValue(error));

    await exchange();
    await vi.advanceTimersByTimeAsync(900_000);

    expect(fakeLog.error).toHaveBeenCalledWith({ err: error }, "idle close failed");
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReconcileDeps } from "../../src/extensions/detached-processes/reconcile.ts";
import { isAlive } from "../../src/extensions/detached-processes/spawn.ts";
import { createWatcherTick } from "../../src/extensions/detached-processes/watcher.ts";
import { createTestContext, insertRunningRecord, type TestContext } from "./setup.ts";

vi.mock("../../src/extensions/detached-processes/spawn.ts", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../src/extensions/detached-processes/spawn.ts")>();
  return { ...actual, isAlive: vi.fn(actual.isAlive) };
});

const aliveMock = vi.mocked(isAlive);

let ctx: TestContext;
let logError: ReturnType<typeof vi.fn>;
let deps: ReconcileDeps;

beforeEach(async () => {
  ctx = await createTestContext();
  logError = vi.fn();
  deps = { ...ctx.reconcile, log: { ...ctx.reconcile.log, error: logError } };
  aliveMock.mockReset();
  aliveMock.mockImplementation(() => false);
});

describe("createWatcherTick", () => {
  it("reconciles records whose pid is no longer alive", async () => {
    const record = insertRunningRecord(ctx, 424242, { name: "ghost" });
    aliveMock.mockReturnValue(false);

    await createWatcherTick(deps)();

    expect(ctx.repository.get(record.id)?.status).toBe("exited");
    expect(logError).not.toHaveBeenCalled();
  });

  it("leaves records whose pid is still alive untouched", async () => {
    const record = insertRunningRecord(ctx, 424243, { name: "alive" });
    aliveMock.mockReturnValue(true);

    await createWatcherTick(deps)();

    expect(ctx.repository.get(record.id)?.status).toBe("running");
  });

  it("isolates per-record errors so one bad record does not stop the sweep", async () => {
    const boom = insertRunningRecord(ctx, 424244, { name: "boom" });
    const good = insertRunningRecord(ctx, 424245, { name: "good" });

    aliveMock.mockImplementation((pid: number) => {
      if (pid === boom.pid) throw new Error("liveness probe failed");
      return false;
    });

    await createWatcherTick(deps)();

    expect(logError).toHaveBeenCalledWith(
      expect.objectContaining({ id: boom.id }),
      "watcher: error checking record",
    );
    expect(ctx.repository.get(good.id)?.status).toBe("exited");
  });
});

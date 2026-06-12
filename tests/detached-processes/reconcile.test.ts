import { spawnSync } from "node:child_process";

import { beforeEach, describe, expect, it } from "vitest";

import { reconcileOnStartup } from "../../src/extensions/detached-processes/reconcile.ts";
import { createTestContext, insertRunningRecord, type TestContext } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
});

const deadPid = (): number => {
  const result = spawnSync("true");

  if (result.pid == null || result.pid === 0) throw new Error("failed to obtain a dead pid");

  return result.pid;
};

describe("reconcileOnStartup", () => {
  it("marks records with dead pids as exited with a null exit code", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "ghost" });

    await reconcileOnStartup(ctx.reconcile);

    const updated = ctx.repository.get(record.id);
    expect(updated?.status).toBe("exited");
    // No sidecar exists for a non-child: the exit code is unknowable.
    expect(updated?.exitCode).toBeNull();
    expect(updated?.exitedAt).not.toBeNull();

    // Crash recovery never notifies — no burst on restart.
    expect(ctx.notifications).toHaveLength(0);
  });

  it("leaves records with alive pids running", async () => {
    const record = insertRunningRecord(ctx, process.pid, { name: "alive" });

    await reconcileOnStartup(ctx.reconcile);

    expect(ctx.repository.get(record.id)?.status).toBe("running");
  });
});

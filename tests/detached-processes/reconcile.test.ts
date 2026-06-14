import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  reconcileExit,
  reconcileOnStartup,
} from "../../src/extensions/detached-processes/reconcile.ts";
import { STOP_REASON_AGENT_STOPPED } from "../../src/extensions/detached-processes/schema.ts";
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

const writeExitCode = async (context: TestContext, id: string, code: string): Promise<void> => {
  const dir = join(context.processesDir, id);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, "exit-code"), code);
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

describe("reconcileExit", () => {
  it("returns early for a record that is already exited", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "done" });
    ctx.repository.reconcileToExited(record.id, new Date(), 0);

    await reconcileExit(ctx.reconcile, record.id);

    expect(ctx.notifications).toHaveLength(0);
  });

  it("returns early for an unknown record", async () => {
    await reconcileExit(ctx.reconcile, "missing");

    expect(ctx.notifications).toHaveLength(0);
  });

  it("suppresses the notification for an agent-stopped process even when it wins the race", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "stopped" });
    ctx.repository.markStopInitiated(record.id);

    await reconcileExit(ctx.reconcile, record.id);

    expect(ctx.repository.get(record.id)?.status).toBe("exited");
    expect(ctx.repository.get(record.id)?.stopReason).toBe(STOP_REASON_AGENT_STOPPED);
    expect(ctx.notifications).toHaveLength(0);
  });

  it("reports an unknown exit code when the sidecar holds a non-numeric value", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "garbled" });
    await writeExitCode(ctx, record.id, "not-a-number");

    await reconcileExit(ctx.reconcile, record.id);

    expect(ctx.repository.get(record.id)?.exitCode).toBeNull();
    expect(ctx.notifications[0]?.message).toBe(
      `Process 'garbled' (id: ${record.id}) exited with code unknown.`,
    );
  });

  it("logs and swallows errors thrown while reconciling", async () => {
    const error = new Error("db unavailable");
    const logError = vi.fn();
    const repository = {
      get: () => {
        throw error;
      },
    } as unknown as typeof ctx.repository;

    await expect(
      reconcileExit({ ...ctx.reconcile, repository, log: { ...ctx.log, error: logError } }, "any"),
    ).resolves.toBeUndefined();

    expect(logError).toHaveBeenCalledWith({ id: "any", err: error }, "error reconciling process");
  });
});

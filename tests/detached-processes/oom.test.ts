import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { ScopeInspector } from "../../src/extensions/detached-processes/cgroup.ts";
import { reconcileExit } from "../../src/extensions/detached-processes/reconcile.ts";
import { STOP_REASON_OOM_KILLED } from "../../src/extensions/detached-processes/schema.ts";
import { handleQueryProcess } from "../../src/extensions/detached-processes/tools.ts";
import { createTestContext, insertRunningRecord, type TestContext } from "./setup.ts";

const deadPid = (): number => {
  const result = spawnSync("true");

  if (result.pid == null || result.pid === 0) throw new Error("failed to obtain a dead pid");

  return result.pid;
};

const writeExitCode = async (ctx: TestContext, id: string, code: string): Promise<void> => {
  const dir = join(ctx.processesDir, id);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, "exit-code"), code);
};

describe("OOM attribution in reconcileExit", () => {
  it("attributes a 137 on a limited process to the OOM killer", async () => {
    const ctx = await createTestContext({
      readMemoryCurrentMb: async () => null,
      wasOomKilled: async () => true,
    });

    const record = insertRunningRecord(ctx, deadPid(), { name: "hungry", memoryLimitMb: 256 });
    await writeExitCode(ctx, record.id, "137");

    await reconcileExit(ctx.reconcile, record.id);

    const updated = ctx.repository.get(record.id);
    expect(updated?.status).toBe("exited");
    expect(updated?.exitCode).toBe(137);
    expect(updated?.stopReason).toBe(STOP_REASON_OOM_KILLED);

    expect(ctx.notifications).toHaveLength(1);
    expect(ctx.notifications[0]).toMatchObject({
      severity: "urgent",
      message: `Process 'hungry' (id: ${record.id}) was killed by the OOM killer (256MB limit).`,
    });
  });

  it("reports a plain SIGKILL when a 137 was not an OOM kill", async () => {
    const ctx = await createTestContext({
      readMemoryCurrentMb: async () => null,
      wasOomKilled: async () => false,
    });

    const record = insertRunningRecord(ctx, deadPid(), { name: "killed", memoryLimitMb: 256 });
    await writeExitCode(ctx, record.id, "137");

    await reconcileExit(ctx.reconcile, record.id);

    const updated = ctx.repository.get(record.id);
    expect(updated?.stopReason).toBeNull();
    expect(ctx.notifications[0]?.message).toBe(
      `Process 'killed' (id: ${record.id}) was killed by signal (SIGKILL).`,
    );
  });

  it("does not consult the scope for an unlimited process", async () => {
    let consulted = false;
    const ctx = await createTestContext({
      readMemoryCurrentMb: async () => null,
      wasOomKilled: async () => {
        consulted = true;
        return true;
      },
    });

    const record = insertRunningRecord(ctx, deadPid(), { name: "free", memoryLimitMb: null });
    await writeExitCode(ctx, record.id, "137");

    await reconcileExit(ctx.reconcile, record.id);

    expect(consulted).toBe(false);
    expect(ctx.repository.get(record.id)?.stopReason).toBeNull();
    expect(ctx.notifications[0]?.message).toBe(
      `Process 'free' (id: ${record.id}) was killed by signal (SIGKILL).`,
    );
  });
});

describe("query_process memory reporting", () => {
  const usageInspector = (mb: number | null): ScopeInspector => ({
    readMemoryCurrentMb: async () => mb,
    wasOomKilled: async () => false,
  });

  it("surfaces live memory usage alongside the limit for a running process", async () => {
    const ctx = await createTestContext(usageInspector(23));
    const record = insertRunningRecord(ctx, process.pid, { name: "live", memoryLimitMb: 128 });

    const details = await handleQueryProcess(ctx.toolDeps, { process_id: record.id });

    expect(details).toContain("- Memory limit: 128MB");
    expect(details).toContain("- Memory usage: 23MB");
  });

  it("omits the usage line when no reading is available", async () => {
    const ctx = await createTestContext(usageInspector(null));
    const record = insertRunningRecord(ctx, process.pid, { name: "noread", memoryLimitMb: 128 });

    const details = await handleQueryProcess(ctx.toolDeps, { process_id: record.id });

    expect(details).toContain("- Memory limit: 128MB");
    expect(details).not.toContain("- Memory usage:");
  });

  it("marks an exited OOM-killed process in its details", async () => {
    const ctx = await createTestContext();
    const record = insertRunningRecord(ctx, deadPid(), { name: "gone", memoryLimitMb: 64 });

    ctx.repository.reconcileToExited(record.id, new Date(), 137, STOP_REASON_OOM_KILLED);

    const details = await handleQueryProcess(ctx.toolDeps, { process_id: record.id });

    expect(details).toContain("- Status: exited");
    expect(details).toContain("- Stopped: OOM-killed");
  });
});

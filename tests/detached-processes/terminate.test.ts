import { beforeEach, describe, expect, it } from "vitest";

import { spawnProcess, terminate } from "../../src/extensions/detached-processes/spawn.ts";
import { handleTerminateProcess } from "../../src/extensions/detached-processes/tools.ts";
import { isAlive } from "../../src/util/is-alive.ts";
import { createTestContext, type TestContext, waitFor } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
});

describe("terminate flow", () => {
  it("stops a sleeping process with SIGTERM and records the exit", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "sleeper", command: "sleep 30" });

    const message = await handleTerminateProcess(ctx.toolDeps, {
      process_id: record.id,
      grace_seconds: 3,
    });

    expect(message).toBe(`Process 'sleeper' stopped (exit code: 143).`);
    expect(isAlive(record.pid)).toBe(false);

    const updated = ctx.repository.get(record.id);
    expect(updated?.status).toBe("exited");
    expect(updated?.exitCode).toBe(143);

    // Agent-stopped exits must not notify the user.
    expect(ctx.notifications).toHaveLength(0);
  });

  it("escalates to SIGKILL when the process group ignores SIGTERM", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "stubborn",
      command: "trap '' TERM; while true; do sleep 0.1; done",
    });

    await terminate(record, ctx.log, { graceSeconds: 0.5 });

    await waitFor(() => !isAlive(record.pid));
  });

  it("reports an already-exited process without signalling", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "quick", command: "true" });

    await waitFor(() => !isAlive(record.pid));

    const message = await handleTerminateProcess(ctx.toolDeps, { process_id: record.id });

    expect(message).toBe(`Process 'quick' already stopped (exit code: 0).`);
    expect(ctx.repository.get(record.id)?.status).toBe("exited");
    expect(ctx.notifications).toHaveLength(0);
  });

  it("rejects unknown signals", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "sleeper", command: "sleep 30" });

    await expect(
      handleTerminateProcess(ctx.toolDeps, { process_id: record.id, signal: "SIGBOGUS" }),
    ).rejects.toThrow(/Unknown signal/);

    await terminate(record, ctx.log, { graceSeconds: 1 });
  });
});

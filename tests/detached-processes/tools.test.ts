import { beforeEach, describe, expect, it } from "vitest";

import { isAlive, spawnProcess } from "../../src/extensions/detached-processes/spawn.ts";
import {
  handleDispatchProcess,
  handleQueryProcess,
  handleReadProcessOutput,
} from "../../src/extensions/detached-processes/tools.ts";
import { createTestContext, type TestContext, waitFor } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
});

describe("handleDispatchProcess", () => {
  it("spawns a process and reports its identifiers", async () => {
    const message = await handleDispatchProcess(ctx.toolDeps, {
      name: "greeter",
      command: "echo hi; sleep 0.1",
    });

    expect(message).toContain("Process 'greeter' started.");
    expect(message).toMatch(/- ID: /);
    expect(message).toMatch(/- PID: \d+/);

    const records = ctx.repository.listRunning();
    expect(records).toHaveLength(1);

    await waitFor(() => !isAlive(records[0]?.pid ?? 0));
  });

  it("rejects a memory limit below 1 MB", async () => {
    await expect(
      handleDispatchProcess(ctx.toolDeps, { name: "x", command: "true", memory_limit_mb: 0 }),
    ).rejects.toThrow(/Invalid memory_limit_mb/);
  });
});

describe("handleQueryProcess", () => {
  it("returns an empty message when nothing is running", async () => {
    expect(await handleQueryProcess(ctx.toolDeps, {})).toBe("No running processes found.");
  });

  it("lazy-reconciles dead processes out of the running list", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "quick", command: "echo done" });

    await waitFor(() => !isAlive(record.pid));

    expect(await handleQueryProcess(ctx.toolDeps, {})).toBe("No running processes found.");
    expect(ctx.repository.get(record.id)?.status).toBe("exited");
    expect(ctx.notifications).toHaveLength(1);

    const archived = await handleQueryProcess(ctx.toolDeps, { archived: true });
    expect(archived).toContain(`[${record.id}] **quick**`);
    expect(archived).toContain("(code: 0)");
  });

  it("returns full details for a specific process", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "detail", command: "sleep 0.1" });

    const details = await handleQueryProcess(ctx.toolDeps, { process_id: record.id });

    expect(details).toContain("# detail");
    expect(details).toContain(`- ID: ${record.id}`);
    expect(details).toContain(`- PID: ${record.pid}`);
    expect(details).toContain("- Status: running");
    expect(details).toContain(`- Command: sleep 0.1`);

    await waitFor(() => !isAlive(record.pid));

    const afterExit = await handleQueryProcess(ctx.toolDeps, { process_id: record.id });
    expect(afterExit).toContain("- Status: exited");
    expect(afterExit).toContain("- Exit code: 0");
  });

  it("throws for an unknown process id", async () => {
    await expect(handleQueryProcess(ctx.toolDeps, { process_id: "nope" })).rejects.toThrow(
      /Process 'nope' not found/,
    );
  });
});

describe("handleReadProcessOutput", () => {
  it("reads stdout by default and stderr on request", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "noisy",
      command: "echo out; echo err >&2",
    });

    await waitFor(() => !isAlive(record.pid));

    expect(await handleReadProcessOutput(ctx.toolDeps, { process_id: record.id })).toBe("out\n");
    expect(
      await handleReadProcessOutput(ctx.toolDeps, { process_id: record.id, stream: "stderr" }),
    ).toBe("err\n");
  });

  it("reports when there is no output", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "silent", command: "true" });

    await waitFor(() => !isAlive(record.pid));

    expect(await handleReadProcessOutput(ctx.toolDeps, { process_id: record.id })).toBe(
      "No output yet.",
    );
  });

  it("throws for an unknown process id", async () => {
    await expect(handleReadProcessOutput(ctx.toolDeps, { process_id: "nope" })).rejects.toThrow(
      /not found/,
    );
  });
});

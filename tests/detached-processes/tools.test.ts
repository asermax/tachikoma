import { totalmem } from "node:os";
import { beforeEach, describe, expect, it } from "vitest";

import { isAlive, spawnProcess } from "../../src/extensions/detached-processes/spawn.ts";
import {
  handleDeleteProcess,
  handleDispatchProcess,
  handleQueryProcess,
  handleReadProcessOutput,
  handleRenameProcess,
  handleTerminateProcess,
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

  it("rejects a memory limit larger than total system RAM", async () => {
    const overSystem = Math.floor(totalmem() / (1024 * 1024)) + 1024;

    await expect(
      handleDispatchProcess(ctx.toolDeps, {
        name: "x",
        command: "true",
        memory_limit_mb: overSystem,
      }),
    ).rejects.toThrow(/Exceeds total system RAM/);
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

  it("reads a windowed slice with offset and count", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "counter",
      command: "for i in 1 2 3 4 5; do echo line$i; done",
    });

    await waitFor(() => !isAlive(record.pid));

    expect(
      await handleReadProcessOutput(ctx.toolDeps, { process_id: record.id, offset: 1, count: 2 }),
    ).toBe("line2\nline3");
  });

  it("windows the stderr stream when selected", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "errs",
      command: "for i in a b c; do echo err$i >&2; done",
    });

    await waitFor(() => !isAlive(record.pid));

    expect(
      await handleReadProcessOutput(ctx.toolDeps, {
        process_id: record.id,
        stream: "stderr",
        offset: 0,
        count: 2,
      }),
    ).toBe("erra\nerrb");
  });

  it("reports when the window starts past the end of the log", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "short",
      command: "echo only-line",
    });

    await waitFor(() => !isAlive(record.pid));

    expect(
      await handleReadProcessOutput(ctx.toolDeps, { process_id: record.id, offset: 50, count: 10 }),
    ).toMatch(/No output at lines 50-60 \(log has 1 lines\)\./);
  });

  it("rejects a negative offset", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "x", command: "echo hi" });

    await waitFor(() => !isAlive(record.pid));

    await expect(
      handleReadProcessOutput(ctx.toolDeps, { process_id: record.id, offset: -1 }),
    ).rejects.toThrow(/Invalid offset/);
  });

  it("throws for an unknown process id", async () => {
    await expect(handleReadProcessOutput(ctx.toolDeps, { process_id: "nope" })).rejects.toThrow(
      /not found/,
    );
  });
});

describe("handleRenameProcess", () => {
  it("renames a process record's display name", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "old-name", command: "sleep 0.1" });

    const message = await handleRenameProcess(ctx.toolDeps, {
      process_id: record.id,
      name: "new-name",
    });

    expect(message).toBe("Process renamed to 'new-name'.");
    expect(ctx.repository.get(record.id)?.name).toBe("new-name");

    await waitFor(() => !isAlive(record.pid));
  });

  it("rejects a blank name", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "keep", command: "sleep 0.1" });

    await expect(
      handleRenameProcess(ctx.toolDeps, { process_id: record.id, name: "   " }),
    ).rejects.toThrow(/must not be empty/);

    expect(ctx.repository.get(record.id)?.name).toBe("keep");

    await waitFor(() => !isAlive(record.pid));
  });

  it("throws for an unknown process id", async () => {
    await expect(
      handleRenameProcess(ctx.toolDeps, { process_id: "nope", name: "whatever" }),
    ).rejects.toThrow(/not found/);
  });
});

describe("handleDeleteProcess", () => {
  it("drops an exited process record", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "ephemeral", command: "true" });

    await waitFor(() => !isAlive(record.pid));
    await handleTerminateProcess(ctx.toolDeps, { process_id: record.id, grace_seconds: 0 });

    const message = await handleDeleteProcess(ctx.toolDeps, { process_id: record.id });

    expect(message).toContain("deleted");
    expect(ctx.repository.get(record.id)).toBeNull();
  });

  it("refuses to delete a still-running process", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "alive", command: "sleep 5" });

    await expect(handleDeleteProcess(ctx.toolDeps, { process_id: record.id })).rejects.toThrow(
      /still running/,
    );

    expect(ctx.repository.get(record.id)).not.toBeNull();

    await handleTerminateProcess(ctx.toolDeps, { process_id: record.id, grace_seconds: 0 });
    await waitFor(() => !isAlive(record.pid));
  });

  it("throws for an unknown process id", async () => {
    await expect(handleDeleteProcess(ctx.toolDeps, { process_id: "nope" })).rejects.toThrow(
      /not found/,
    );
  });
});

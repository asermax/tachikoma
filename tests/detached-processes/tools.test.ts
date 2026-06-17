import { spawnSync } from "node:child_process";
import { totalmem } from "node:os";

import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as spawnModule from "../../src/extensions/detached-processes/spawn.ts";
import { isAlive, spawnProcess } from "../../src/extensions/detached-processes/spawn.ts";
import {
  createProcessToolsFactory,
  handleDeleteProcess,
  handleDispatchProcess,
  handleQueryProcess,
  handleReadProcessOutput,
  handleRenameProcess,
  handleTerminateProcess,
} from "../../src/extensions/detached-processes/tools.ts";
import { createTestContext, insertRunningRecord, type TestContext, waitFor } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
});

const deadPid = (): number => {
  const result = spawnSync("true");

  if (result.pid == null || result.pid === 0) throw new Error("failed to obtain a dead pid");

  return result.pid;
};

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

  it("lists a live running record while reconciling a dead one out", async () => {
    const alive = insertRunningRecord(ctx, process.pid, { name: "alive" });
    const dead = await spawnProcess(ctx.spawnDeps, { name: "dead", command: "true" });

    await waitFor(() => !isAlive(dead.pid));

    const listing = await handleQueryProcess(ctx.toolDeps, {});

    expect(listing).toContain(`[${alive.id}] **alive**`);
    expect(listing).not.toContain(`[${dead.id}]`);
    expect(ctx.repository.get(dead.id)?.status).toBe("exited");
  });

  it("reports no exited processes when the archive is empty", async () => {
    expect(await handleQueryProcess(ctx.toolDeps, { archived: true })).toBe(
      "No exited processes found.",
    );
  });

  it("throws when the record vanishes after a lazy reconcile", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "vanishing" });

    const realGet = ctx.repository.get.bind(ctx.repository);
    let calls = 0;
    const repository = {
      get: (id: string) => {
        calls += 1;
        return calls === 1 ? realGet(id) : null;
      },
    } as unknown as typeof ctx.repository;

    await expect(
      handleQueryProcess({ ...ctx.toolDeps, repository }, { process_id: record.id }),
    ).rejects.toThrow(/not found/);
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

  it("rejects a count below 1", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "x", command: "echo hi" });

    await waitFor(() => !isAlive(record.pid));

    await expect(
      handleReadProcessOutput(ctx.toolDeps, { process_id: record.id, count: 0 }),
    ).rejects.toThrow(/Invalid count/);
  });

  it("reports no output for a windowed read of an empty log", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "empty", command: "true" });

    await waitFor(() => !isAlive(record.pid));

    expect(
      await handleReadProcessOutput(ctx.toolDeps, { process_id: record.id, offset: 0, count: 10 }),
    ).toBe("No output yet.");
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

describe("handleTerminateProcess edge cases", () => {
  it("throws for an unknown process id", async () => {
    await expect(handleTerminateProcess(ctx.toolDeps, { process_id: "nope" })).rejects.toThrow(
      /not found/,
    );
  });

  it("lazy-reconciles a running record whose pid is already dead", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "ghost" });

    const message = await handleTerminateProcess(ctx.toolDeps, { process_id: record.id });

    expect(message).toBe(`Process 'ghost' already stopped (exit code: unknown).`);
    expect(ctx.repository.get(record.id)?.status).toBe("exited");
    expect(ctx.notifications).toHaveLength(0);
  });

  it("reports an exited record without signalling", async () => {
    const record = insertRunningRecord(ctx, deadPid(), { name: "done" });
    ctx.repository.reconcileToExited(record.id, new Date(), 7);

    const message = await handleTerminateProcess(ctx.toolDeps, { process_id: record.id });

    expect(message).toBe(`Process 'done' already stopped (exit code: 7).`);
  });

  it("signals anyway when marking stop-initiated fails", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "stubborn", command: "sleep 30" });

    const markError = new Error("db locked");
    const logWarn = vi.fn();
    const repository = Object.assign(Object.create(Object.getPrototypeOf(ctx.repository)), {
      get: ctx.repository.get.bind(ctx.repository),
      markStopInitiated: () => {
        throw markError;
      },
    }) as typeof ctx.repository;

    const message = await handleTerminateProcess(
      { ...ctx.toolDeps, repository, log: { ...ctx.log, warn: logWarn } },
      { process_id: record.id, grace_seconds: 3 },
    );

    expect(logWarn).toHaveBeenCalledWith(
      { id: record.id, err: markError },
      "failed to mark stop initiated — signalling anyway",
    );
    expect(message).toContain("stopped");

    await waitFor(() => !isAlive(record.pid));
  });

  it("translates an EPERM from terminate into a permission-denied error", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "denied", command: "sleep 30" });

    const epermError = Object.assign(new Error("operation not permitted"), { code: "EPERM" });
    const spy = vi.spyOn(spawnModule, "terminate").mockRejectedValue(epermError);

    try {
      await expect(handleTerminateProcess(ctx.toolDeps, { process_id: record.id })).rejects.toThrow(
        /Permission denied: cannot signal process/,
      );
    } finally {
      spy.mockRestore();
    }

    expect(ctx.repository.get(record.id)?.stopReason).toBeNull();

    process.kill(record.pid, "SIGKILL");
    await waitFor(() => !isAlive(record.pid));
  });

  it("still surfaces permission-denied when clearing the stop reason also fails", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "stuck", command: "sleep 30" });

    const epermError = Object.assign(new Error("operation not permitted"), { code: "EPERM" });
    const spy = vi.spyOn(spawnModule, "terminate").mockRejectedValue(epermError);
    const repository = Object.assign(Object.create(Object.getPrototypeOf(ctx.repository)), {
      get: ctx.repository.get.bind(ctx.repository),
      markStopInitiated: ctx.repository.markStopInitiated.bind(ctx.repository),
      clearStopReason: () => {
        throw new Error("also broken");
      },
    }) as typeof ctx.repository;

    try {
      await expect(
        handleTerminateProcess({ ...ctx.toolDeps, repository }, { process_id: record.id }),
      ).rejects.toThrow(/Permission denied/);
    } finally {
      spy.mockRestore();
    }

    process.kill(record.pid, "SIGKILL");
    await waitFor(() => !isAlive(record.pid));
  });

  it("rethrows a non-EPERM error from terminate", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "boom", command: "sleep 30" });

    const otherError = Object.assign(new Error("kaboom"), { code: "EIO" });
    const spy = vi.spyOn(spawnModule, "terminate").mockRejectedValue(otherError);

    try {
      await expect(handleTerminateProcess(ctx.toolDeps, { process_id: record.id })).rejects.toThrow(
        /kaboom/,
      );
    } finally {
      spy.mockRestore();
    }

    process.kill(record.pid, "SIGKILL");
    await waitFor(() => !isAlive(record.pid));
  });

  it("returns immediately after signalling when grace is 0", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "fire", command: "sleep 30" });

    const message = await handleTerminateProcess(ctx.toolDeps, {
      process_id: record.id,
      grace_seconds: 0,
    });

    expect(message).toBe(`Signal sent to process 'fire'.`);

    await waitFor(() => !isAlive(record.pid));
  });
});

describe("createProcessToolsFactory", () => {
  it("registers all detached process tools and routes their execute handlers", async () => {
    type Registered = { name: string; execute: (id: string, params: unknown) => Promise<unknown> };
    const tools: Registered[] = [];
    const pi = { registerTool: (tool: Registered) => tools.push(tool) };

    createProcessToolsFactory(ctx.toolDeps)(pi as unknown as Parameters<ExtensionFactory>[0]);

    expect(tools.map((tool) => tool.name)).toEqual([
      "dispatch_detached_process",
      "query_process",
      "read_process_output",
      "rename_process",
      "delete_process",
      "terminate_process",
    ]);

    const byName = (name: string): Registered => {
      const tool = tools.find((entry) => entry.name === name);

      if (tool == null) throw new Error(`tool ${name} not registered`);

      return tool;
    };

    const dispatchResult = (await byName("dispatch_detached_process").execute("call-1", {
      name: "viaTool",
      command: "echo hi; sleep 0.1",
    })) as { content: { type: string; text: string }[] };

    expect(dispatchResult.content[0]?.text).toContain("Process 'viaTool' started.");

    const queryResult = (await byName("query_process").execute("call-2", {})) as {
      content: { text: string }[];
    };
    expect(queryResult.content[0]?.text).toContain("viaTool");

    const record = ctx.repository.listRunning()[0];

    if (record == null) throw new Error("expected a running record");

    const renameResult = (await byName("rename_process").execute("call-3", {
      process_id: record.id,
      name: "renamed",
    })) as { content: { text: string }[] };
    expect(renameResult.content[0]?.text).toContain("renamed");

    const outputResult = (await byName("read_process_output").execute("call-4", {
      process_id: record.id,
    })) as { content: { text: string }[] };
    expect(typeof outputResult.content[0]?.text).toBe("string");

    await waitFor(() => !isAlive(record.pid));

    const terminateResult = (await byName("terminate_process").execute("call-5", {
      process_id: record.id,
    })) as { content: { text: string }[] };
    expect(terminateResult.content[0]?.text).toContain("already stopped");

    const deleteResult = (await byName("delete_process").execute("call-6", {
      process_id: record.id,
    })) as { content: { text: string }[] };
    expect(deleteResult.content[0]?.text).toContain("deleted");
  });
});

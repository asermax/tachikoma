import { EventEmitter } from "node:events";
import { readFileSync, rmSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { spawnMock } = vi.hoisted(() => ({ spawnMock: vi.fn() }));

vi.mock("node:child_process", async () => {
  const actual = await vi.importActual<typeof import("node:child_process")>("node:child_process");

  return { ...actual, spawn: spawnMock };
});

import {
  exitCodePath,
  processDir,
  spawnProcess,
} from "../../src/extensions/detached-processes/spawn.ts";
import { createTestContext, type TestContext } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
  spawnMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

interface FakeChild extends EventEmitter {
  pid: number | undefined;
  unref: () => void;
}

const makeFakeChild = (pid: number | undefined): FakeChild => {
  const child = new EventEmitter() as FakeChild;
  child.pid = pid;
  child.unref = () => {};
  return child;
};

const spawnWithFakeChild = (child: FakeChild): void => {
  spawnMock.mockImplementation(() => {
    queueMicrotask(() => child.emit("spawn"));
    return child;
  });
};

describe("spawnProcess internals (mocked child)", () => {
  it("throws when the spawned child has no pid", async () => {
    spawnWithFakeChild(makeFakeChild(undefined));

    await expect(spawnProcess(ctx.spawnDeps, { name: "nopid", command: "true" })).rejects.toThrow(
      /spawned child has no pid/,
    );
  });

  it("writes a signal-derived sidecar code when the child exits via signal", async () => {
    const child = makeFakeChild(424242);
    spawnWithFakeChild(child);

    const record = await spawnProcess(ctx.spawnDeps, { name: "sig", command: "true" });

    child.emit("exit", null, "SIGKILL");

    expect(readFileSync(exitCodePath(ctx.processesDir, record.id), "utf-8")).toBe("137");
  });

  it("writes an empty sidecar when neither code nor signal is available", async () => {
    const child = makeFakeChild(424243);
    spawnWithFakeChild(child);

    const record = await spawnProcess(ctx.spawnDeps, { name: "neither", command: "true" });

    child.emit("exit", null, null);

    expect(readFileSync(exitCodePath(ctx.processesDir, record.id), "utf-8")).toBe("");
  });

  it("logs a warning when the exit-code sidecar cannot be written", async () => {
    const child = makeFakeChild(424244);
    spawnWithFakeChild(child);
    const warn = vi.fn();

    const record = await spawnProcess(
      { ...ctx.spawnDeps, log: { ...ctx.log, warn } },
      { name: "unwritable", command: "true" },
    );

    rmSync(processDir(ctx.processesDir, record.id), { recursive: true, force: true });

    child.emit("exit", 0, null);

    expect(warn).toHaveBeenCalledWith(
      expect.objectContaining({ pid: 424244 }),
      "failed to write exit-code sidecar",
    );
  });

  it("logs an error when the detached child emits an error after spawn", async () => {
    const child = makeFakeChild(424245);
    spawnWithFakeChild(child);
    const error = vi.fn();

    await spawnProcess(
      { ...ctx.spawnDeps, log: { ...ctx.log, error } },
      { name: "errs", command: "true" },
    );

    const childError = new Error("pipe broke");
    child.emit("error", childError);

    expect(error).toHaveBeenCalledWith(
      expect.objectContaining({ pid: 424245, err: childError }),
      "detached child emitted an error",
    );
  });

  it("wraps the command so the child writes its own exit code to the sidecar", async () => {
    const child = makeFakeChild(424246);
    spawnWithFakeChild(child);

    await spawnProcess(ctx.spawnDeps, { name: "wrapped", command: "the-user-command" });

    expect(spawnMock).toHaveBeenCalledTimes(1);
    const [, args] = spawnMock.mock.calls[0] as [string, string[], unknown];
    expect(args[0]).toBe("-c");
    const script = args[1];
    // The user command passes through verbatim after the EXIT trap…
    expect(script).toContain("EXIT; the-user-command");
    // …and the trap body writes the captured code to an absolute sidecar path.
    expect(script).toContain("__tachikoma_rc=$?");
    expect(script).toContain("printf %s");
    expect(script).toContain(ctx.processesDir);
  });
});

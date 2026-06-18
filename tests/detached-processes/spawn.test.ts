import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProcessLimiter } from "../../src/extensions/detached-processes/limits.ts";
import {
  exitCodePath,
  isAlive,
  spawnProcess,
  terminate,
  wrapWithExitCapture,
} from "../../src/extensions/detached-processes/spawn.ts";
import { createWatcherTick } from "../../src/extensions/detached-processes/watcher.ts";
import { createTestContext, type TestContext, waitFor } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
});

const deadPid = (): number => {
  const result = spawnSync("true");

  if (result.pid == null || result.pid === 0) throw new Error("failed to obtain a dead pid");

  return result.pid;
};

describe("spawnProcess", () => {
  it("captures output and the watcher tick detects the exit", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "hello",
      command: "echo hi; echo oops >&2; sleep 0.1",
    });

    expect(record.status).toBe("running");
    expect(ctx.repository.listRunning()).toHaveLength(1);

    await waitFor(() => !isAlive(record.pid));
    await createWatcherTick(ctx.reconcile)();

    const updated = ctx.repository.get(record.id);
    expect(updated?.status).toBe("exited");
    expect(updated?.exitCode).toBe(0);
    expect(updated?.exitedAt).not.toBeNull();

    expect(await readFile(record.stdoutPath, "utf-8")).toBe("hi\n");
    expect(await readFile(record.stderrPath, "utf-8")).toBe("oops\n");

    expect(ctx.notifications).toHaveLength(1);
    expect(ctx.notifications[0]).toMatchObject({
      processId: record.id,
      severity: "info",
      message: `Process 'hello' (id: ${record.id}) exited with code 0.`,
    });
  });

  it("notifies with warning severity on a non-zero exit", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "failing", command: "exit 3" });

    await waitFor(() => !isAlive(record.pid));
    await createWatcherTick(ctx.reconcile)();

    expect(ctx.repository.get(record.id)?.exitCode).toBe(3);
    expect(ctx.notifications[0]).toMatchObject({ severity: "warning" });
  });

  it("respects the requested working directory and env overrides", async () => {
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "env",
      command: "pwd; printf '%s\\n' \"$GREETING\"",
      cwd: ctx.processesDir,
      env: { GREETING: "bonjour" },
    });

    await waitFor(() => !isAlive(record.pid));

    expect(await readFile(record.stdoutPath, "utf-8")).toBe(`${ctx.processesDir}\nbonjour\n`);
  });

  it("rejects blank names and commands", async () => {
    await expect(spawnProcess(ctx.spawnDeps, { name: "  ", command: "true" })).rejects.toThrow(
      /name must not be empty/,
    );
    await expect(spawnProcess(ctx.spawnDeps, { name: "x", command: " " })).rejects.toThrow(
      /command must not be empty/,
    );
  });

  const limitedLimiter: ProcessLimiter = {
    wrap: (_id, command) => ({ file: "sh", args: ["-c", command], limited: true }),
  };

  it("invokes the onExit callback and records the limit when the command is wrapped as limited", async () => {
    const onExit = vi.fn();

    const record = await spawnProcess(
      { ...ctx.spawnDeps, limiter: limitedLimiter, onExit },
      { name: "limited", command: "true", memoryLimitMb: 64 },
    );

    expect(record.memoryLimitMb).toBe(64);

    await waitFor(() => onExit.mock.calls.length > 0);
    expect(onExit).toHaveBeenCalledWith(record.id);
  });

  it("records a null limit when wrapped as limited but no limit was requested", async () => {
    const record = await spawnProcess(
      { ...ctx.spawnDeps, limiter: limitedLimiter },
      { name: "limited-null", command: "true", memoryLimitMb: null },
    );

    expect(record.memoryLimitMb).toBeNull();

    await waitFor(() => !isAlive(record.pid));
  });

  it("writes a signal-derived exit code to the sidecar when killed by a signal", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "signalled", command: "sleep 30" });

    process.kill(record.pid, "SIGKILL");

    await waitFor(() => !isAlive(record.pid));
    await createWatcherTick(ctx.reconcile)();

    expect(ctx.repository.get(record.id)?.exitCode).toBe(137);
    expect(await readFile(exitCodePath(ctx.processesDir, record.id), "utf-8")).toBe("137");
  });

  it("kills the process group and rethrows when the db write fails after spawn", async () => {
    const error = new Error("db write failed");
    const repository = {
      ...ctx.repository,
      create: vi.fn(() => {
        throw error;
      }),
    } as unknown as typeof ctx.repository;

    await expect(
      spawnProcess({ ...ctx.spawnDeps, repository }, { name: "doomed", command: "sleep 30" }),
    ).rejects.toThrow(/db write failed/);
  });
});

describe("terminate", () => {
  it("returns immediately when the process group is already gone", async () => {
    await expect(
      terminate({ pid: deadPid() }, ctx.log, { graceSeconds: 1 }),
    ).resolves.toBeUndefined();
  });

  it("returns after signalling when graceSeconds is 0", async () => {
    const record = await spawnProcess(ctx.spawnDeps, { name: "fire", command: "sleep 30" });

    await terminate(record, ctx.log, { graceSeconds: 0 });

    await waitFor(() => !isAlive(record.pid));
  });

  it("escalates to SIGKILL after the grace period when SIGTERM is ignored", async () => {
    const warn = vi.fn();
    const record = await spawnProcess(ctx.spawnDeps, {
      name: "stubborn",
      // Install the TERM trap before signalling readiness so the grace loop is
      // guaranteed to observe a live, signal-ignoring leader even under load.
      command: "trap '' TERM; echo ready; while true; do sleep 1; done",
    });

    await waitFor(
      () =>
        existsSync(record.stdoutPath) && readFileSync(record.stdoutPath, "utf-8").includes("ready"),
    );

    await terminate(record, { ...ctx.log, warn }, { graceSeconds: 1 });

    expect(warn).toHaveBeenCalled();
    await waitFor(() => !isAlive(record.pid));
  });
});

describe("wrapWithExitCapture", () => {
  it("writes the command's exit code to the sidecar itself, with no host listener", async () => {
    const sidecar = join(ctx.processesDir, "wrap-exit-code");

    // Run the wrapped command through a real shell with no host exit listener,
    // proving the sidecar is written by the child itself — durable across a host
    // restart. `exit 3` confirms the EXIT trap fires and captures the code.
    const result = spawnSync("sh", ["-c", wrapWithExitCapture("exit 3", sidecar)]);

    expect(result.status).toBe(3);
    expect(await readFile(sidecar, "utf-8")).toBe("3");
  });

  it("captures a clean exit and propagates it as the wrapper's own status", async () => {
    const sidecar = join(ctx.processesDir, "wrap-exit-code");

    const result = spawnSync("sh", ["-c", wrapWithExitCapture("true", sidecar)]);

    expect(result.status).toBe(0);
    expect(await readFile(sidecar, "utf-8")).toBe("0");
  });
});

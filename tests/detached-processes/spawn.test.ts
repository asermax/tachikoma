import { readFile } from "node:fs/promises";

import { beforeEach, describe, expect, it } from "vitest";

import { isAlive, spawnProcess } from "../../src/extensions/detached-processes/spawn.ts";
import { createWatcherTick } from "../../src/extensions/detached-processes/watcher.ts";
import { createTestContext, type TestContext, waitFor } from "./setup.ts";

let ctx: TestContext;

beforeEach(async () => {
  ctx = await createTestContext();
});

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
});

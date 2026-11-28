import { type ChildProcess, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProcessRestarter, type RestarterDeps } from "../../src/extensions/self-update/seams.ts";
import type { Logger } from "../../src/log.ts";
import { type BootRecord, RESTART_BOOT } from "./setup.ts";

const ARGS = ["--channel", "telegram", "two words"];
const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

const tempDir = async (): Promise<string> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-self-update-"));
  tempDirs.push(dir);
  return dir;
};

const fakeLog = { warn: vi.fn() } as unknown as Logger;

/** Records the call and throws, since a successful exec never returns. */
const recordingExecve = () =>
  vi.fn((): never => {
    throw new Error("__execve__");
  });

const deps = (overrides: Partial<RestarterDeps>): RestarterDeps => ({
  execve: recordingExecve(),
  execPath: process.execPath,
  argv: ["node", "main.js"],
  env: { PATH: "/bin" },
  ...overrides,
});

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

interface Fixture {
  /** The process the test started: with an in-place re-exec, the successor is this same process. */
  child: ChildProcess;
  /** Resolves with the exit code once the fixture — or its successor — exits. */
  exited: Promise<number | null>;
  stdout: () => string;
  stderr: () => string;
}

/** Run the restart fixture under the test runner's node, with the given environment. */
const launch = (env: Record<string, string>, args: string[] = ARGS): Fixture => {
  const child = spawn(process.execPath, [RESTART_BOOT.script, ...args], {
    env: { ...process.env, ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let out = "";
  let err = "";
  child.stdout?.setEncoding("utf8").on("data", (chunk: string) => {
    out += chunk;
  });
  child.stderr?.setEncoding("utf8").on("data", (chunk: string) => {
    err += chunk;
  });

  return {
    child,
    exited: new Promise((resolve) => child.on("exit", (code) => resolve(code))),
    stdout: () => out,
    stderr: () => err,
  };
};

/** Poll `condition`; a fixture that exits first fails the wait at once. */
const waitFor = async (
  fixture: Fixture,
  condition: () => boolean,
  timeoutMs = 10_000,
): Promise<void> => {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    // `exitCode` stays null when a process dies by a signal — how a failed exec ends on
    // Node < 26.1 — so both are checked.
    if (fixture.child.exitCode !== null || fixture.child.signalCode !== null) {
      throw new Error(
        `restart fixture exited early (code ${fixture.child.exitCode}, signal ${fixture.child.signalCode})\n${fixture.stderr()}`,
      );
    }
    if (condition()) return;
    await sleep(20);
  }

  throw new Error(`condition not met within ${timeoutMs}ms\n${fixture.stderr()}`);
};

const readRecords = async (path: string): Promise<BootRecord[]> =>
  (await readFile(path, "utf8"))
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => JSON.parse(line) as BootRecord);

describe("ProcessRestarter", () => {
  it("replaces the process in place: the successor keeps the pid, the parent and the process group", async () => {
    const dir = await tempDir();
    const record = join(dir, "record.jsonl");
    const ready = join(dir, "ready");
    const release = join(dir, "release");
    const fixture = launch({
      [RESTART_BOOT.record]: record,
      [RESTART_BOOT.ready]: ready,
      [RESTART_BOOT.release]: release,
      [RESTART_BOOT.exitCode]: "7",
    });

    try {
      await waitFor(fixture, () => existsSync(ready));
      // The process this test started is still the one running, and it writes to the
      // stream the test gave it: nothing exited and nothing was left behind.
      expect(fixture.child.exitCode).toBeNull();
      await waitFor(fixture, () => fixture.stdout().includes(RESTART_BOOT.stdoutLine));

      const [predecessor, successor, ...extra] = await readRecords(record);
      expect(extra).toEqual([]);
      expect(predecessor?.boot).toBe(0);
      expect(successor?.boot).toBe(1);
      expect(predecessor?.pid).toBe(fixture.child.pid);
      expect(successor?.pid).toBe(fixture.child.pid);
      expect(predecessor?.ppid).not.toBe(fixture.child.pid);
      expect(successor?.ppid).toBe(predecessor?.ppid);
      // Without `/proc` (not every platform has it) the pid equality above is all that
      // an unchanged process group can be observed through.
      if (predecessor?.pgid != null) expect(successor?.pgid).toBe(predecessor.pgid);
    } finally {
      await writeFile(release, "go");
    }

    // The exit code is the successor's: the launcher saw one process the whole time.
    expect(await fixture.exited).toBe(7);
  }, 20_000);

  it("hands the successor the same command line and an environment variable assigned during the run", async () => {
    const record = join(await tempDir(), "record.jsonl");
    const fixture = launch({ [RESTART_BOOT.record]: record });

    const code = await fixture.exited;
    expect(code, fixture.stderr()).toBe(0);

    const [predecessor, successor] = await readRecords(record);
    expect(predecessor?.argv).toEqual(ARGS);
    expect(successor?.argv).toEqual(ARGS);
    expect(predecessor?.assigned).toMatch(/^[0-9a-f]{16}$/);
    expect(successor?.carried).toBe(predecessor?.assigned);
  }, 20_000);

  it("lets the successor take the workspace lock its predecessor still held", async () => {
    const dir = await tempDir();
    const lock = join(dir, "instance.json");
    const record = join(dir, "record.jsonl");
    const fixture = launch({ [RESTART_BOOT.record]: record, [RESTART_BOOT.lock]: lock });

    // A boot-time rollback re-execs without releasing: the lock still records this pid.
    const code = await fixture.exited;
    expect(code, fixture.stderr()).toBe(0);

    const [, successor] = await readRecords(record);
    expect(successor?.lock).toBe("acquired");
    expect(JSON.parse(await readFile(lock, "utf8"))).toMatchObject({ pid: fixture.child.pid });
  }, 20_000);

  it("re-execs the given command line and environment", () => {
    const execve = recordingExecve();
    const argv = ["/usr/bin/node", "/app/main.js", "--channel", "telegram"];
    const env = { PATH: "/bin", HOME: "/home/tachikoma" };
    const log = { warn: vi.fn() } as unknown as Logger;

    expect(() =>
      new ProcessRestarter(log, { execve, execPath: "/usr/bin/node", argv, env }).restart(),
    ).toThrow("__execve__");

    expect(execve).toHaveBeenCalledWith("/usr/bin/node", argv, env);
    expect(log.warn).toHaveBeenCalledWith(
      { execPath: "/usr/bin/node" },
      "re-executing process to load the new version",
    );
  });

  it("throws instead of exec-ing when the node executable is missing", () => {
    const execve = recordingExecve();
    const execPath = join(tmpdir(), "tachi-no-such-node", "node");

    expect(() => new ProcessRestarter(fakeLog, deps({ execve, execPath })).restart()).toThrow(
      `cannot re-exec ${execPath}`,
    );
    expect(execve).not.toHaveBeenCalled();
  });

  it("throws instead of exec-ing when the node executable is not executable", async () => {
    const execve = recordingExecve();
    const execPath = join(await tempDir(), "node");
    await writeFile(execPath, "not a program", { mode: 0o644 });

    expect(() => new ProcessRestarter(fakeLog, deps({ execve, execPath })).restart()).toThrow(
      `cannot re-exec ${execPath}`,
    );
    expect(execve).not.toHaveBeenCalled();
  });

  it("throws instead of exec-ing on a platform without process.execve", () => {
    // Falling back to the platform's real execve here would replace the test runner.
    expect(() => new ProcessRestarter(fakeLog, deps({ execve: undefined })).restart()).toThrow(
      /process\.execve is unavailable/,
    );
  });
});

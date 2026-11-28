import { type ChildProcess, spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProcessRestarter, type RestarterDeps } from "../../src/extensions/self-update/seams.ts";
import { fakeLogger } from "../git/helpers.ts";
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

const fakeLog = fakeLogger();

/** Records the call and throws, since a successful exec never returns. */
const recordingExecve = () =>
  vi.fn((): never => {
    throw new Error("__execve__");
  });

const deps = (execve: typeof process.execve, execPath = process.execPath): RestarterDeps => ({
  execve,
  execPath,
  argv: [execPath, "main.js"],
  env: { PATH: "/bin" },
});

interface Fixture {
  /** The process the test started: with an in-place re-exec, the successor is this same process. */
  child: ChildProcess;
  /** Resolves with the exit code once the fixture — or its successor — exited and its pipes closed. */
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
    // `close`, not `exit`: it fires once the pipes are drained, so `stdout()` is complete.
    exited: new Promise((resolve) => child.on("close", (code) => resolve(code))),
    stdout: () => out,
    stderr: () => err,
  };
};

const readRecords = async (path: string): Promise<BootRecord[]> =>
  (await readFile(path, "utf8"))
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => JSON.parse(line) as BootRecord);

describe("ProcessRestarter", () => {
  it("replaces the process in place: the successor keeps the pid, the parent and the stdio", async () => {
    const record = join(await tempDir(), "record.jsonl");
    const fixture = launch({ [RESTART_BOOT.record]: record, [RESTART_BOOT.exitCode]: "7" });

    // The launcher saw one process the whole time: the exit code is the successor's, and the
    // successor's output arrived on the pipe the test gave its predecessor.
    expect(await fixture.exited, fixture.stderr()).toBe(7);
    expect(fixture.stdout()).toContain(RESTART_BOOT.stdoutLine);

    const [predecessor, successor] = await readRecords(record);
    expect(predecessor?.pid).toBe(fixture.child.pid);
    expect(successor?.pid).toBe(fixture.child.pid);
    // Same parent: no pre-restart process is left alive as the successor's parent.
    expect(successor?.ppid).toBe(predecessor?.ppid);
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

    expect(() =>
      new ProcessRestarter(fakeLog, { execve, execPath: "/usr/bin/node", argv, env }).restart(),
    ).toThrow("__execve__");

    expect(execve).toHaveBeenCalledWith("/usr/bin/node", argv, env);
    expect(fakeLog.warn).toHaveBeenCalledWith(
      { execPath: "/usr/bin/node" },
      "re-executing process to load the new version",
    );
  });

  it("throws instead of exec-ing when the node executable is missing", () => {
    const execve = recordingExecve();
    const execPath = join(tmpdir(), "tachi-no-such-node", "node");

    expect(() => new ProcessRestarter(fakeLog, deps(execve, execPath)).restart()).toThrow(
      `cannot re-exec ${execPath}`,
    );
    expect(execve).not.toHaveBeenCalled();
  });

  it("throws instead of exec-ing when the node executable is not executable", async () => {
    const execve = recordingExecve();
    const execPath = join(await tempDir(), "node");
    await writeFile(execPath, "not a program", { mode: 0o644 });

    expect(() => new ProcessRestarter(fakeLog, deps(execve, execPath)).restart()).toThrow(
      `cannot re-exec ${execPath}`,
    );
    expect(execve).not.toHaveBeenCalled();
  });

  it("throws instead of exec-ing on a platform without process.execve", () => {
    // Falling back to the platform's real execve here would replace the test runner.
    expect(() => new ProcessRestarter(fakeLog, deps(undefined)).restart()).toThrow(
      /process\.execve is unavailable/,
    );
  });
});

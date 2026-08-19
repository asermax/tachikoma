import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acquireInstanceLock,
  InstanceConflictError,
  type InstanceLock,
  type InstanceLockDeps,
  type ProcStat,
  parseInstanceInfo,
} from "../src/instance-lock.ts";

const HOLDER_PID = 4242;
const HOLDER_INFO = {
  pid: HOLDER_PID,
  startedAt: "2026-08-08T09:12:34.000Z",
  startTimeTicks: 100,
  command: "node /usr/local/bin/tachikoma",
};

/** Deps with faked pid lifecycle — the notify spy doubles as the diagnostic sink. */
const fakeDeps = (
  overrides: {
    isAlive?: (pid: number) => boolean;
    readProcStat?: (pid: number) => ProcStat | null;
    afterCreate?: InstanceLockDeps["afterCreate"];
  } = {},
) => ({
  isAlive: overrides.isAlive ?? vi.fn(() => true),
  // Own /proc stat by default; holders get null unless a test says otherwise.
  readProcStat:
    overrides.readProcStat ??
    vi.fn((pid: number) => (pid === process.pid ? { state: "S", startTimeTicks: 1 } : null)),
  notify: vi.fn(),
  afterCreate: overrides.afterCreate,
});

const writeLock = (lockPath: string, content: unknown) =>
  writeFile(lockPath, typeof content === "string" ? content : JSON.stringify(content), "utf8");

const readLock = (lockPath: string) => readFile(lockPath, "utf8");

describe("parseInstanceInfo", () => {
  it("accepts a complete record", () => {
    expect(parseInstanceInfo(JSON.stringify(HOLDER_INFO))).toEqual(HOLDER_INFO);
  });

  it("accepts a record without startTimeTicks", () => {
    const { startTimeTicks: _, ...withoutTicks } = HOLDER_INFO;
    expect(parseInstanceInfo(JSON.stringify(withoutTicks))).toEqual(withoutTicks);
  });

  it.each([
    ["malformed JSON", "{not json"],
    ["empty file", ""],
    ["whitespace only", "   \n"],
    ["not an object", '"a string"'],
    ["missing pid", JSON.stringify({ startedAt: "x", command: "y" })],
    ["non-integer pid", JSON.stringify({ ...HOLDER_INFO, pid: "4242" })],
    ["non-positive pid", JSON.stringify({ ...HOLDER_INFO, pid: 0 })],
    ["missing startedAt", JSON.stringify({ pid: 1, command: "y" })],
    ["missing command", JSON.stringify({ pid: 1, startedAt: "x" })],
    ["non-integer startTimeTicks", JSON.stringify({ ...HOLDER_INFO, startTimeTicks: 1.5 })],
  ])("rejects %s", (_label, raw) => {
    expect(parseInstanceInfo(raw)).toBeNull();
  });
});

describe("instance lock files", () => {
  let dir: string;
  let lockPath: string;
  /** Set by each test that acquires; released automatically in afterEach. */
  let lock: InstanceLock | undefined;

  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "tachi-lock-"));
    lockPath = join(dir, "instance.json");
    lock = undefined;
  });

  afterEach(async () => {
    await lock?.release();
    await rm(dir, { recursive: true, force: true });
  });

  describe("acquireInstanceLock", () => {
    it("creates the lock with this process's identity (AC: fresh start)", async () => {
      const deps = fakeDeps();

      lock = await acquireInstanceLock(lockPath, deps);

      const recorded = parseInstanceInfo(await readLock(lockPath));
      expect(recorded).toEqual({
        pid: process.pid,
        startedAt: expect.any(String) as string,
        startTimeTicks: 1,
        command: process.argv.join(" "),
      });
      expect(recorded?.startedAt).toEqual(new Date(recorded?.startedAt ?? "").toISOString()); // ISO
    });

    it("omits startTimeTicks when /proc is unavailable", async () => {
      const deps = fakeDeps({ readProcStat: () => null });

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.startTimeTicks).toBeUndefined();
    });

    it("throws before any takeover when a live instance holds the lock, identifying it (AC1)", async () => {
      await writeLock(lockPath, HOLDER_INFO);
      const deps = fakeDeps({
        readProcStat: () => ({ state: "S", startTimeTicks: HOLDER_INFO.startTimeTicks }),
      });

      const attempt = acquireInstanceLock(lockPath, deps);

      await expect(attempt).rejects.toBeInstanceOf(InstanceConflictError);
      await expect(attempt).rejects.toThrow(/4242/);
      await expect(attempt).rejects.toThrow(HOLDER_INFO.startedAt);
      await expect(attempt).rejects.toThrow(HOLDER_INFO.command);
      await expect(attempt).rejects.toThrow(lockPath);
      expect(deps.isAlive).toHaveBeenCalledWith(HOLDER_PID);
      // The holder's lock is left untouched.
      expect(parseInstanceInfo(await readLock(lockPath))).toEqual(HOLDER_INFO);
    });

    it("treats a live holder as a conflict when /proc is unavailable (AC1)", async () => {
      await writeLock(lockPath, HOLDER_INFO);
      const deps = fakeDeps({ readProcStat: () => null });

      await expect(acquireInstanceLock(lockPath, deps)).rejects.toThrow(InstanceConflictError);
      expect(parseInstanceInfo(await readLock(lockPath))).toEqual(HOLDER_INFO);
    });

    it("takes over a dead holder's stale lock (AC2)", async () => {
      await writeLock(lockPath, HOLDER_INFO);
      const deps = fakeDeps({ isAlive: () => false });

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.pid).toBe(process.pid);
      expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("pid 4242 is dead"));
    });

    it("takes over when the pid was reused by another process (AC3)", async () => {
      await writeLock(lockPath, HOLDER_INFO);
      const deps = fakeDeps({
        readProcStat: (pid) =>
          pid === HOLDER_PID
            ? { state: "S", startTimeTicks: 777 }
            : { state: "S", startTimeTicks: 1 },
      });

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.pid).toBe(process.pid);
      expect(deps.notify).toHaveBeenCalledWith(
        expect.stringContaining("reused by another process"),
      );
    });

    it("takes over when the holder is an unreaped zombie (AC3)", async () => {
      await writeLock(lockPath, HOLDER_INFO);
      const deps = fakeDeps({
        readProcStat: (pid) =>
          pid === HOLDER_PID
            ? { state: "Z", startTimeTicks: HOLDER_INFO.startTimeTicks }
            : { state: "S", startTimeTicks: 1 },
      });

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.pid).toBe(process.pid);
      expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("unreaped zombie"));
    });

    it("reclaims the lock when it records our own pid (re-exec) (AC4)", async () => {
      await writeLock(lockPath, { ...HOLDER_INFO, pid: process.pid });
      const deps = fakeDeps();

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.pid).toBe(process.pid);
      expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("own pid"));
    });

    it.each([
      ["malformed JSON", "{not json"],
      ["empty file", ""],
      ["mistyped fields", JSON.stringify({ pid: "4242", startedAt: 1, command: null })],
    ])("takes over a corrupt lock: %s (AC5)", async (_label, raw) => {
      await writeLock(lockPath, raw);
      const deps = fakeDeps();

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.pid).toBe(process.pid);
      expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("corrupt"));
    });

    it("refuses when a racing writer replaces our fresh lock content (AC8)", async () => {
      const deps = fakeDeps({
        readProcStat: () => ({ state: "S", startTimeTicks: 100 }),
        afterCreate: (path) => writeLock(path, { ...HOLDER_INFO, pid: 5555, startTimeTicks: 100 }),
      });

      const attempt = acquireInstanceLock(lockPath, deps);

      await expect(attempt).rejects.toBeInstanceOf(InstanceConflictError);
      await expect(attempt).rejects.toThrow(/5555/);
    });

    it("recovers when the verification read finds the file gone (raced away)", async () => {
      let once = true;
      const deps = fakeDeps({
        afterCreate: async (path) => {
          if (once) {
            once = false;
            await rm(path, { force: true });
          }
        },
      });

      lock = await acquireInstanceLock(lockPath, deps);

      expect(parseInstanceInfo(await readLock(lockPath))?.pid).toBe(process.pid);
    });

    it("gives up after bounded attempts against a churning lock file", async () => {
      const deps = fakeDeps({
        afterCreate: (path) => rm(path, { force: true }), // every create is undone
      });

      await expect(acquireInstanceLock(lockPath, deps)).rejects.toThrow(
        `failed to acquire instance lock at ${lockPath} after 5 attempts`,
      );
    });
  });

  describe("InstanceLock.release", () => {
    it("removes the lock file and is idempotent", async () => {
      lock = await acquireInstanceLock(lockPath, fakeDeps());

      await lock.release();
      await lock.release();

      await expect(readLock(lockPath)).rejects.toMatchObject({ code: "ENOENT" });
    });

    it("leaves a successor's lock in place with a warning (AC11)", async () => {
      const deps = fakeDeps();
      lock = await acquireInstanceLock(lockPath, deps);

      const successor = { ...HOLDER_INFO, pid: 9999 };
      await writeLock(lockPath, successor);
      await lock.release();

      expect(parseInstanceInfo(await readLock(lockPath))).toEqual(successor);
      expect(deps.notify).toHaveBeenCalledWith(expect.stringContaining("no longer records pid"));
    });
  });
});

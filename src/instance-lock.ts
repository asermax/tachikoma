import { readFileSync } from "node:fs";
import { type FileHandle, open, readFile, unlink, writeFile } from "node:fs/promises";

import { isAlive } from "./util/is-alive.ts";

/** Records who holds the workspace's single-instance lock. */
export interface InstanceInfo {
  pid: number;
  /** When this instance booted (lock acquisition time), ISO 8601. */
  startedAt: string;
  /** `/proc/<pid>/stat` field 22 (clock ticks since boot) — process identity across pid reuse; absent without `/proc`. */
  startTimeTicks?: number;
  command: string;
}

/** `/proc/<pid>/stat` fields used for holder identity (see `readProcStat`). */
export interface ProcStat {
  state: string;
  startTimeTicks: number;
}

export interface InstanceLockDeps {
  isAlive?: (pid: number) => boolean;
  readProcStat?: (pid: number) => ProcStat | null;
  /** Diagnostic sink — stderr by default, since acquisition precedes logger creation. */
  notify?: (message: string) => void;
  /** Test seam: invoked between the exclusive create and the verification read, to simulate a racing writer. */
  afterCreate?: (lockPath: string) => Promise<void> | void;
}

/**
 * Reads `/proc/<pid>/stat` for holder identity: `state` (field 3 — `Z` marks an exited
 * but unreaped zombie) and `startTimeTicks` (field 22 — unique per process lifetime, so
 * a mismatch means the pid was reused). Returns null when `/proc` is unavailable
 * (non-Linux) or the process vanished — callers then decide on liveness alone.
 */
const readProcStat = (pid: number): ProcStat | null => {
  try {
    const raw = readFileSync(`/proc/${pid}/stat`, "utf8");
    // comm (field 2) may contain spaces and parens — everything after the LAST ')' is
    // safe to split; field 3 becomes token 0 and field 22 becomes token 19.
    const close = raw.lastIndexOf(")");
    if (close === -1) return null;
    const fields = raw.slice(close + 2).split(/\s+/);
    const state = fields[0];
    const startTimeTicks = Number(fields[19]);
    if (state == null || state === "" || !Number.isFinite(startTimeTicks)) return null;
    return { state, startTimeTicks };
  } catch {
    return null;
  }
};

/** Strict parse of lock-file content; anything else is corruption (null). */
export const parseInstanceInfo = (raw: string): InstanceInfo | null => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;

  const { pid, startedAt, startTimeTicks, command } = parsed as Record<string, unknown>;
  if (typeof pid !== "number" || !Number.isInteger(pid) || pid <= 0) return null;
  if (typeof startedAt !== "string" || startedAt === "") return null;
  if (typeof command !== "string") return null;
  if (
    startTimeTicks !== undefined &&
    (typeof startTimeTicks !== "number" || !Number.isInteger(startTimeTicks))
  ) {
    return null;
  }

  const info: InstanceInfo = { pid, startedAt, command };
  if (startTimeTicks !== undefined) info.startTimeTicks = startTimeTicks;
  return info;
};

/** Startup failure: a live instance already holds the workspace. */
export class InstanceConflictError extends Error {
  constructor(holder: InstanceInfo, lockPath: string) {
    super(
      `another tachikoma instance is already running against this workspace:
  pid:      ${holder.pid}
  started:  ${holder.startedAt}
  command:  ${holder.command}
lock file: ${lockPath}
Stop that process first, or (if it is long gone) remove the lock file.`,
    );
    this.name = "InstanceConflictError";
  }
}

type LockState = { kind: "missing" } | { kind: "corrupt" } | { kind: "held"; info: InstanceInfo };

const errno = (error: unknown): string | undefined => (error as NodeJS.ErrnoException).code;

const readLockState = async (lockPath: string): Promise<LockState> => {
  let raw: string;
  try {
    raw = await readFile(lockPath, "utf8");
  } catch (error) {
    if (errno(error) !== "ENOENT") throw error;
    return { kind: "missing" };
  }
  const info = parseInstanceInfo(raw);
  return info == null ? { kind: "corrupt" } : { kind: "held", info };
};

const ownInfo = (readProcStatDep: (pid: number) => ProcStat | null): InstanceInfo => {
  const stat = readProcStatDep(process.pid);
  const info: InstanceInfo = {
    pid: process.pid,
    startedAt: new Date().toISOString(),
    command: process.argv.join(" "),
  };
  if (stat != null) info.startTimeTicks = stat.startTimeTicks;
  return info;
};

/** Unlink only if still present — a racing takeover may have removed it first. */
const unlinkIfExists = async (lockPath: string): Promise<void> => {
  try {
    await unlink(lockPath);
  } catch (error) {
    if (errno(error) !== "ENOENT") throw error;
  }
};

/**
 * Unlink only while the path still shows what we decided to remove: between the inspecting
 * read and the unlink a racing process may have replaced the file with its own valid lock —
 * that file is theirs, so leave it and let the next attempt inspect it.
 */
const removeIfStill = async (lockPath: string, decided: "corrupt" | number): Promise<boolean> => {
  const state = await readLockState(lockPath);
  const still =
    decided === "corrupt"
      ? state.kind === "corrupt"
      : state.kind === "held" && state.info.pid === decided;
  if (!still) return false;
  await unlinkIfExists(lockPath);
  return true;
};

/**
 * Exclusive create + verify: write our identity, then only claim the lock when the path
 * still records our pid — a racing writer that replaced the content, or a torn write,
 * loses this attempt (the full re-parse, not a substring match, decides).
 */
const tryCreate = async (
  lockPath: string,
  own: InstanceInfo,
  afterCreate?: (lockPath: string) => Promise<void> | void,
): Promise<boolean> => {
  let handle: FileHandle;
  try {
    handle = await open(lockPath, "wx");
  } catch (error) {
    if (errno(error) === "EEXIST") return false;
    throw error;
  }

  try {
    await writeFile(handle, `${JSON.stringify(own, null, 2)}\n`, "utf8");
  } finally {
    await handle.close();
  }

  if (afterCreate != null) await afterCreate(lockPath);

  const state = await readLockState(lockPath);
  return state.kind === "held" && state.info.pid === process.pid;
};

const MAX_ATTEMPTS = 5;

/**
 * Acquires the workspace's single-instance lock, or throws `InstanceConflictError` when a
 * live instance holds it. The lock is a small JSON identity file created exclusively
 * (`wx`): the winner is whoever's content is still at the path after a verify read, which
 * makes the stale-takeover race safe without inode tricks — the loser detects the foreign
 * content and refuses. Stale locks (dead pid, pid reuse, zombie, corrupt file, own pid
 * after a re-exec) are taken over automatically.
 */
export const acquireInstanceLock = async (
  lockPath: string,
  deps: InstanceLockDeps = {},
): Promise<InstanceLock> => {
  const isAliveDep = deps.isAlive ?? isAlive;
  const readProcStatDep = deps.readProcStat ?? readProcStat;
  const notify = deps.notify ?? console.error;
  // Identity is constant for this process's lifetime — compute once, not per attempt.
  const own = ownInfo(readProcStatDep);

  /** Remove the lock only while it still shows what we decided to remove; report takeovers. */
  const takeOver = async (decided: "corrupt" | number, message: string): Promise<void> => {
    if (await removeIfStill(lockPath, decided)) notify(message);
  };

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    // 1) Exclusive create + verify: only proceed when the path still records our pid —
    //    a racing writer that replaced the content loses us the acquisition.
    if (await tryCreate(lockPath, own, deps.afterCreate)) return new InstanceLock(lockPath, notify);

    // 2) Something occupies the path — inspect and decide.
    const state = await readLockState(lockPath);
    if (state.kind === "missing") continue; // raced away — try to create again

    if (state.kind === "corrupt") {
      await takeOver("corrupt", `replacing corrupt instance lock at ${lockPath}`);
      continue;
    }

    const { info } = state;
    if (info.pid === process.pid) {
      // A re-exec kept this pid — reclaim it.
      await takeOver(info.pid, `reclaiming instance lock at ${lockPath} from own pid ${info.pid}`);
      continue;
    }

    if (!isAliveDep(info.pid)) {
      await takeOver(
        info.pid,
        `removing stale instance lock at ${lockPath} — pid ${info.pid} is dead`,
      );
      continue;
    }

    const stat = readProcStatDep(info.pid);
    if (stat == null) throw new InstanceConflictError(info, lockPath); // no /proc: liveness alone decides
    if (stat.state === "Z") {
      await takeOver(
        info.pid,
        `removing stale instance lock at ${lockPath} — pid ${info.pid} is an unreaped zombie`,
      );
      continue;
    }
    if (info.startTimeTicks != null && stat.startTimeTicks !== info.startTimeTicks) {
      await takeOver(
        info.pid,
        `removing stale instance lock at ${lockPath} — pid ${info.pid} was reused by another process`,
      );
      continue;
    }

    throw new InstanceConflictError(info, lockPath);
  }

  throw new Error(`failed to acquire instance lock at ${lockPath} after ${MAX_ATTEMPTS} attempts`);
};

/**
 * The acquired single-instance lock. `release()` is idempotent and ownership-checked: it
 * removes the lock file only while it still records our pid, never a successor's.
 */
export class InstanceLock {
  private released = false;
  private readonly lockPath: string;
  private readonly notify: (message: string) => void;

  constructor(lockPath: string, notify: (message: string) => void) {
    this.lockPath = lockPath;
    this.notify = notify;
  }

  async release(): Promise<void> {
    if (this.released) return;
    this.released = true;

    const state = await readLockState(this.lockPath);
    if (state.kind !== "held" || state.info.pid !== process.pid) {
      this.notify(
        `instance lock at ${this.lockPath} no longer records pid ${process.pid} — leaving it in place`,
      );
      return;
    }
    await unlinkIfExists(this.lockPath);
  }
}

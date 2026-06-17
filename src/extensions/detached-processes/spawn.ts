import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { closeSync, openSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { constants } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import type { Logger } from "../../log.ts";
import type { ProcessLimiter } from "./limits.ts";
import type { ProcessRepository } from "./repository.ts";
import type { DetachedProcessRecord } from "./schema.ts";

export interface SpawnDeps {
  repository: ProcessRepository;
  limiter: ProcessLimiter;
  /** Root directory holding one subdirectory per process id. */
  processesDir: string;
  log: Logger;
  now?: () => Date;
  /**
   * Called when the child exits while this host is alive, for immediate
   * reconciliation instead of waiting for the next polling sweep. Best-effort:
   * the polling watcher remains the backstop for exits the host misses.
   */
  onExit?: (id: string) => void;
}

export interface SpawnOptions {
  name: string;
  command: string;
  cwd?: string | null;
  env?: Record<string, string> | null;
  memoryLimitMb?: number | null;
}

export const processDir = (processesDir: string, id: string): string => join(processesDir, id);

export const exitCodePath = (processesDir: string, id: string): string =>
  join(processDir(processesDir, id), "exit-code");

/**
 * Liveness via signal 0. EPERM means the pid exists but belongs to another
 * user, so it still counts as alive. No create-time check — see reconcile.ts
 * for why pid reuse is acceptable here.
 */
export const isAlive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
};

const signalExitCode = (signal: NodeJS.Signals): number | null => {
  const number = constants.signals[signal];
  return number != null ? 128 + number : null;
};

/**
 * Spawn a detached shell command with stdout/stderr captured to files and
 * persist the record. The exit-code sidecar file lets reconciliation recover
 * the code while this host process is alive; after a restart the child is
 * reparented and the code is unknowable (null).
 */
export const spawnProcess = async (
  deps: SpawnDeps,
  options: SpawnOptions,
): Promise<DetachedProcessRecord> => {
  const { repository, limiter, processesDir, log } = deps;
  const now = deps.now ?? (() => new Date());

  if (options.name.trim() === "") throw new Error("name must not be empty or whitespace");
  if (options.command.trim() === "") throw new Error("command must not be empty or whitespace");

  const id = randomUUID();
  const dir = processDir(processesDir, id);

  log.debug({ id, name: options.name }, "spawning detached process");

  await mkdir(dir, { recursive: true });

  const stdoutPath = join(dir, "stdout.log");
  const stderrPath = join(dir, "stderr.log");
  const sidecarPath = exitCodePath(processesDir, id);

  const stdoutFd = openSync(stdoutPath, "a");
  const stderrFd = openSync(stderrPath, "a");

  const wrapped = limiter.wrap(id, options.command, options.memoryLimitMb ?? null);
  const cwd = options.cwd ?? process.cwd();

  let child: ReturnType<typeof spawn>;
  try {
    child = spawn(wrapped.file, wrapped.args, {
      cwd,
      env: { ...process.env, ...(options.env ?? {}) },
      detached: true,
      stdio: ["ignore", stdoutFd, stderrFd],
    });

    await new Promise<void>((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
  } finally {
    // The child holds its own copies of the output descriptors.
    closeSync(stdoutFd);
    closeSync(stderrFd);
  }

  if (child.pid == null) throw new Error("spawned child has no pid");

  const pid = child.pid;

  child.on("error", (error) => {
    log.error({ pid, err: error }, "detached child emitted an error");
  });

  child.on("exit", (code, signal) => {
    const value = code ?? (signal != null ? signalExitCode(signal) : null);

    try {
      writeFileSync(sidecarPath, value != null ? String(value) : "");
    } catch (error) {
      log.warn({ pid, err: error }, "failed to write exit-code sidecar");
    }

    deps.onExit?.(id);
  });

  child.unref();

  try {
    const record = repository.create({
      id,
      name: options.name,
      command: options.command,
      cwd,
      pid,
      stdoutPath,
      stderrPath,
      memoryLimitMb: wrapped.limited ? (options.memoryLimitMb ?? null) : null,
      startedAt: now(),
    });

    log.info(
      {
        id,
        pid,
        name: options.name,
        command: options.command,
        memoryLimitMb: wrapped.limited ? (options.memoryLimitMb ?? null) : null,
        limited: wrapped.limited,
      },
      "spawned detached process",
    );

    return record;
  } catch (error) {
    log.error({ pid, err: error }, "db write failed after spawn — killing process group");
    killGroup(pid, "SIGKILL");
    throw error;
  }
};

const killGroup = (pid: number, signal: NodeJS.Signals): "sent" | "gone" => {
  try {
    // detached:true makes the child a process group leader, so -pid reaches
    // the whole tree (wrapper + descendants).
    process.kill(-pid, signal);
    return "sent";
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return "gone";
    throw error;
  }
};

export interface TerminateOptions {
  signal?: NodeJS.Signals;
  /** Seconds to wait before escalating to SIGKILL; 0 = signal and return. */
  graceSeconds?: number;
}

export const terminate = async (
  record: Pick<DetachedProcessRecord, "pid">,
  log: Logger,
  { signal = "SIGTERM", graceSeconds = 10 }: TerminateOptions = {},
): Promise<void> => {
  log.info({ pid: record.pid, signal, graceSeconds }, "terminating detached process");

  if (killGroup(record.pid, signal) === "gone") return;

  if (graceSeconds <= 0) return;

  const pollMs = 100;

  for (let elapsed = 0; elapsed < graceSeconds * 1000; elapsed += pollMs) {
    if (!isAlive(record.pid)) return;
    await sleep(pollMs);
  }

  log.warn({ pid: record.pid, graceSeconds }, "process still alive after grace — sending SIGKILL");

  if (killGroup(record.pid, "SIGKILL") === "gone") return;

  for (let attempt = 0; attempt < 10; attempt += 1) {
    if (!isAlive(record.pid)) return;
    await sleep(pollMs);
  }
};

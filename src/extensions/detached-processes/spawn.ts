import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { closeSync, openSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { constants } from "node:os";
import { isAbsolute, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import type { Logger } from "../../log.ts";
import { isAlive } from "../../util/is-alive.ts";
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

const signalExitCode = (signal: NodeJS.Signals): number | null => {
  const number = constants.signals[signal];
  return number != null ? 128 + number : null;
};

/**
 * Wrap `value` for a POSIX single-quoted context: wrap it in single quotes and
 * escape embedded single quotes via the `'\''` idiom. Safe for paths containing
 * spaces, `$`, or quotes. Applied to the sidecar path and again to the whole
 * trap body, which keeps the user's command string out of every quoted region.
 */
const shSingleQuote = (value: string): string => `'${value.replace(/'/g, "'\\''")}'`;

/**
 * Wrap a user command so the spawned shell writes its own exit code to the
 * sidecar before exiting — recovering the code even when the host was down at
 * exit time, which the host's exit listener cannot cover.
 *
 * An EXIT trap captures `$?` on normal completion *and* on a user `exit N`, and
 * runs in the same shell as the command (not a subshell), so any signal traps
 * the user installs stay in effect on the process-group leader. It does not fire
 * when a signal kills the shell, so signal deaths are still captured only by the
 * host's exit listener (128 + signal). The shell's own exit status equals the
 * command's code, so both writers record the same value. Only the sidecar path is
 * quoted; the user command passes through verbatim after `EXIT; `.
 *
 * `sidecarPath` must be absolute (enforced below): the child may run with a
 * different cwd, and a relative path would be resolved against the child's.
 */
export const wrapWithExitCapture = (command: string, sidecarPath: string): string => {
  if (!isAbsolute(sidecarPath)) {
    throw new Error(`wrapWithExitCapture sidecarPath must be absolute, got: ${sidecarPath}`);
  }
  // `$?` is the shell's exit status at the moment the EXIT trap fires, so reading
  // it inline as the body's first command captures the code without a named var.
  const body = `printf %s "$?" > ${shSingleQuote(sidecarPath)}`;
  return `trap ${shSingleQuote(body)} EXIT; ${command}`;
};

/**
 * Spawn a detached shell command with stdout/stderr captured to files and
 * persist the record. The command is wrapped (see wrapWithExitCapture) so the
 * child self-reports its exit code; the host exit listener is retained for
 * signal deaths and immediate reconcile.
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

  const wrapped = limiter.wrap(
    id,
    wrapWithExitCapture(options.command, sidecarPath),
    options.memoryLimitMb ?? null,
  );
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

  // The EXIT trap never fires on a signal kill, so this listener is the only
  // exit-code writer for signal deaths (128 + signal) and triggers immediate
  // reconcile. Its sidecar write is redundant for normal exits (the trap wrote it).
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

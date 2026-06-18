import { readFile } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";

import type { Logger } from "../../log.ts";
import { type ScopeInspector, scopeUnitName } from "./cgroup.ts";
import type { ProcessRepository } from "./repository.ts";
import { STOP_REASON_AGENT_STOPPED, STOP_REASON_OOM_KILLED } from "./schema.ts";
import { exitCodePath, isAlive } from "./spawn.ts";

export interface ProcessNotification {
  source: string;
  processId: string;
  severity: "info" | "warning" | "urgent";
  message: string;
}

export interface ReconcileDeps {
  repository: ProcessRepository;
  processesDir: string;
  notify: (notification: ProcessNotification) => void;
  scopeInspector: ScopeInspector;
  log: Logger;
  now?: () => Date;
}

const SIGKILL_EXIT_CODE = 137;

const readExitCode = async (path: string): Promise<number | null> => {
  // Retry once after 100ms. A normal exit writes the sidecar from the spawned
  // shell before it dies, so the file is already present once liveness fails;
  // the retry covers signal deaths, where the host's exit listener (the only
  // writer) lags the watcher's kill(pid, 0) observation of death.
  for (const delayMs of [0, 100]) {
    if (delayMs > 0) await sleep(delayMs);

    try {
      const parsed = Number.parseInt(await readFile(path, "utf-8"), 10);
      return Number.isNaN(parsed) ? null : parsed;
    } catch {
      // Missing sidecar — exit happened while the host was down (unknowable code).
    }
  }

  return null;
};

/**
 * Transition a running record to exited, optionally dispatching a notification.
 * Idempotent: a conditional UPDATE makes concurrent reconcilers converge to a
 * single winner, and only the winner notifies.
 */
export const reconcileExit = async (
  deps: ReconcileDeps,
  recordId: string,
  { dispatchNotification = true }: { dispatchNotification?: boolean } = {},
): Promise<void> => {
  const { repository, processesDir, notify, scopeInspector, log } = deps;
  const now = deps.now ?? (() => new Date());

  try {
    const record = repository.get(recordId);

    if (record == null || record.status !== "running") return;

    const exitCode = await readExitCode(exitCodePath(processesDir, record.id));

    // A 137 (SIGKILL) on a limited process may be the OOM killer rather than a
    // plain kill; the scope's `Result` tells them apart even though its cgroup
    // is already gone by now. Only check when a limit was actually enforced.
    const oomKilled =
      exitCode === SIGKILL_EXIT_CODE && record.memoryLimitMb != null
        ? await scopeInspector.wasOomKilled(scopeUnitName(record.id))
        : false;

    const won = repository.reconcileToExited(
      record.id,
      now(),
      exitCode,
      oomKilled ? STOP_REASON_OOM_KILLED : undefined,
    );

    if (!won) return;

    log.info(
      { id: record.id, exitCode, oomKilled, stopReason: record.stopReason },
      "detached process exited",
    );

    if (!dispatchNotification) return;

    if (record.stopReason === STOP_REASON_AGENT_STOPPED) {
      log.debug({ id: recordId }, "suppressing exit notification for agent-stopped process");
      return;
    }

    const limitSuffix = record.memoryLimitMb != null ? ` (${record.memoryLimitMb}MB limit)` : "";

    let message: string;

    if (oomKilled) {
      message = `Process '${record.name}' (id: ${record.id}) was killed by the OOM killer${limitSuffix}.`;
    } else if (exitCode === SIGKILL_EXIT_CODE) {
      message = `Process '${record.name}' (id: ${record.id}) was killed by signal (SIGKILL).`;
    } else {
      message = `Process '${record.name}' (id: ${record.id}) exited with code ${exitCode ?? "unknown"}.`;
    }

    const severity: ProcessNotification["severity"] = oomKilled
      ? "urgent"
      : exitCode === 0
        ? "info"
        : "warning";

    log.debug({ id: record.id, severity, oomKilled }, "dispatching exit notification");

    notify({
      source: `Detached process: ${record.name}`,
      processId: record.id,
      // An OOM kill is operationally significant — surface it as urgent so it
      // interrupts rather than queueing behind ordinary completions.
      severity,
      message,
    });
  } catch (error) {
    log.error({ id: recordId, err: error }, "error reconciling process");
  }
};

/**
 * Crash recovery: reconcile records whose processes died while the host was
 * down. Notifications are suppressed so the user doesn't get a burst on
 * restart. Records whose pids are still alive simply stay running — without a
 * create-time check a reused pid would keep a record alive until its next
 * natural exit, which the polling watcher then reconciles.
 */
export const reconcileOnStartup = async (deps: ReconcileDeps): Promise<void> => {
  for (const record of deps.repository.listRunning()) {
    if (isAlive(record.pid)) continue;

    await reconcileExit(deps, record.id, { dispatchNotification: false });
    deps.log.info({ id: record.id }, "crash recovery: marked process as exited");
  }
};

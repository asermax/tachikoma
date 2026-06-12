import { readFile } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";

import type { Logger } from "../../log.ts";
import type { ProcessRepository } from "./repository.ts";
import { STOP_REASON_AGENT_STOPPED } from "./schema.ts";
import { exitCodePath, isAlive } from "./spawn.ts";

export interface ProcessNotification {
  source: string;
  processId: string;
  severity: "info" | "error";
  message: string;
}

export interface ReconcileDeps {
  repository: ProcessRepository;
  processesDir: string;
  notify: (notification: ProcessNotification) => void;
  log: Logger;
  now?: () => Date;
}

const SIGKILL_EXIT_CODE = 137;

const readExitCode = async (path: string): Promise<number | null> => {
  // Retry once after 100ms: the watcher's kill(pid, 0) can observe death before
  // the in-process exit listener has written the sidecar.
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
  const { repository, processesDir, notify, log } = deps;
  const now = deps.now ?? (() => new Date());

  try {
    const record = repository.get(recordId);

    if (record == null || record.status !== "running") return;

    const exitCode = await readExitCode(exitCodePath(processesDir, record.id));
    const won = repository.reconcileToExited(record.id, now(), exitCode);

    if (!won || !dispatchNotification) return;

    if (record.stopReason === STOP_REASON_AGENT_STOPPED) {
      log.debug({ id: recordId }, "suppressing exit notification for agent-stopped process");
      return;
    }

    const message =
      exitCode === SIGKILL_EXIT_CODE
        ? `Process '${record.name}' (id: ${record.id}) was killed by signal (SIGKILL).`
        : `Process '${record.name}' (id: ${record.id}) exited with code ${exitCode ?? "unknown"}.`;

    notify({
      source: `Detached process: ${record.name}`,
      processId: record.id,
      severity: exitCode === 0 ? "info" : "error",
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

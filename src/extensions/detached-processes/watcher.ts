import { type ReconcileDeps, reconcileExit } from "./reconcile.ts";
import { isAlive } from "./spawn.ts";

/**
 * Polling watcher tick: check every running record for liveness and reconcile
 * the dead ones. Per-record error isolation keeps one bad record from
 * stopping the sweep.
 */
export const createWatcherTick = (deps: ReconcileDeps) => async (): Promise<void> => {
  for (const record of deps.repository.listRunning()) {
    try {
      if (!isAlive(record.pid)) await reconcileExit(deps, record.id);
    } catch (error) {
      deps.log.error({ id: record.id, err: error }, "watcher: error checking record");
    }
  }
};

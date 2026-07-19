import type { KeyValueState } from "../../db/state.ts";

/**
 * Persisted self-update state, kept in the extension's namespaced KV (no table /
 * migration of its own). Four concerns share the namespace:
 *
 *  - `lastCheck`     — checker bookkeeping (when, what latest was seen, what we
 *                      already notified about) so we neither re-notify nor spam.
 *  - `upgrade`       — the in-progress marker written *before* an upgrade restart
 *                      and consumed on the next boot to decide success/rollback.
 *  - `restart`       — the marker written *before* a plain `restart_self` re-exec
 *                      and consumed on the next boot to announce "back online".
 *  - `failedVersion` — the loop guard: a version that rolled back, which we must
 *                      not attempt again until something newer appears.
 */
export interface LastCheckState {
  checkedAt: string;
  latestSeen: string | null;
  /** Latest version the user was already notified about, to avoid repeat notices. */
  notifiedVersion: string | null;
}

export interface UpgradeMarker {
  previousVersion: string;
  targetVersion: string;
  startedAt: string;
}

/**
 * Marker written before a plain `restart_self` re-exec and consumed on the next
 * boot to announce "back online". Carries no version delta (a restart is
 * same-version by construction), so — unlike `UpgradeMarker` — there is no
 * success/rollback distinction to make, only "announce and clear".
 */
export interface RestartMarker {
  startedAt: string;
}

const LAST_CHECK_KEY = "lastCheck";
const UPGRADE_MARKER_KEY = "upgradeMarker";
const RESTART_MARKER_KEY = "restartMarker";
const FAILED_VERSION_KEY = "failedVersion";

export type SelfUpdateStateStore = Pick<KeyValueState, "get" | "set" | "delete">;

/** Thin typed wrapper over the KV namespace; pure decision logic lives elsewhere. */
export class SelfUpdateState {
  private readonly kv: SelfUpdateStateStore;

  constructor(kv: SelfUpdateStateStore) {
    this.kv = kv;
  }

  getLastCheck(): LastCheckState | null {
    return this.kv.get<LastCheckState>(LAST_CHECK_KEY);
  }

  setLastCheck(value: LastCheckState): void {
    this.kv.set(LAST_CHECK_KEY, value);
  }

  getUpgradeMarker(): UpgradeMarker | null {
    return this.kv.get<UpgradeMarker>(UPGRADE_MARKER_KEY);
  }

  setUpgradeMarker(value: UpgradeMarker): void {
    this.kv.set(UPGRADE_MARKER_KEY, value);
  }

  clearUpgradeMarker(): void {
    this.kv.delete(UPGRADE_MARKER_KEY);
  }

  getRestartMarker(): RestartMarker | null {
    return this.kv.get<RestartMarker>(RESTART_MARKER_KEY);
  }

  setRestartMarker(value: RestartMarker): void {
    this.kv.set(RESTART_MARKER_KEY, value);
  }

  clearRestartMarker(): void {
    this.kv.delete(RESTART_MARKER_KEY);
  }

  getFailedVersion(): string | null {
    return this.kv.get<string>(FAILED_VERSION_KEY);
  }

  setFailedVersion(version: string): void {
    this.kv.set(FAILED_VERSION_KEY, version);
  }

  clearFailedVersion(): void {
    this.kv.delete(FAILED_VERSION_KEY);
  }
}

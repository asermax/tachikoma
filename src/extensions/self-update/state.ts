import type { KeyValueState } from "../../db/state.ts";

/**
 * Persisted self-update state, kept in the extension's namespaced KV (no table /
 * migration of its own). Three concerns share the namespace:
 *
 *  - `lastCheck`     — checker bookkeeping (when, what latest was seen, what we
 *                      already notified about) so we neither re-notify nor spam.
 *  - `upgrade`       — the in-progress marker written *before* an upgrade restart
 *                      and consumed on the next boot to decide success/rollback.
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

const LAST_CHECK_KEY = "lastCheck";
const UPGRADE_MARKER_KEY = "upgradeMarker";
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

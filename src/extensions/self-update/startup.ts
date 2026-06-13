import type { Logger } from "../../log.ts";
import { NOTIFY_EVENT, type NotifyPayload } from "../notifications/payload.ts";
import type { NotifyEmitter } from "./checker.ts";
import { decideStartup, STARTUP_ACTIONS } from "./decisions.ts";
import type { Installer, Restarter } from "./seams.ts";
import type { SelfUpdateState } from "./state.ts";

export interface StartupDeps {
  installer: Installer;
  restarter: Restarter;
  state: SelfUpdateState;
  currentVersion: string;
  emit: NotifyEmitter;
  log: Logger;
}

const notify = (emit: NotifyEmitter, payload: Omit<NotifyPayload, "source">): void => {
  emit(NOTIFY_EVENT, { ...payload, source: "self-update" });
};

/**
 * Boot-time reconciliation of any in-progress upgrade marker.
 *
 *  - No marker → clean boot, nothing to do.
 *  - Marker present and we're now running the target version → the upgrade
 *    landed: announce "back online", clear the marker AND the loop guard (a
 *    successful run supersedes any earlier failure).
 *  - Marker present but we're NOT on the target → the new version failed to come
 *    up cleanly. Record the target as failed (loop guard), reinstall the previous
 *    version, clear the marker, then re-exec back onto the known-good build. The
 *    "back online" notice is surfaced by the *next* boot's success path.
 *
 * The marker is cleared before any restart so a rollback that itself fails to
 * boot does not loop forever — the next boot sees no marker and stays put.
 */
export const reconcileStartup = async ({
  installer,
  restarter,
  state,
  currentVersion,
  emit,
  log,
}: StartupDeps): Promise<void> => {
  const decision = decideStartup(state.getUpgradeMarker(), currentVersion);

  if (decision.action === STARTUP_ACTIONS.none) return;

  const marker = decision.marker as NonNullable<typeof decision.marker>;

  if (decision.action === STARTUP_ACTIONS.upgradeSucceeded) {
    log.info(
      { from: marker.previousVersion, to: marker.targetVersion },
      "upgrade succeeded; clearing marker",
    );

    state.clearUpgradeMarker();
    state.clearFailedVersion();

    notify(emit, {
      title: "Back online",
      text: `Tachikoma is back online after upgrading from ${marker.previousVersion} to ${marker.targetVersion}.`,
      severity: "info",
    });

    return;
  }

  log.error(
    { running: currentVersion, target: marker.targetVersion, previous: marker.previousVersion },
    "upgrade target is not running; rolling back",
  );

  state.setFailedVersion(marker.targetVersion);
  state.clearUpgradeMarker();

  try {
    await installer.install(marker.previousVersion);
  } catch (error) {
    log.error({ err: error }, "rollback install failed; staying on current process");
    notify(emit, {
      title: "Update rollback failed",
      text: `An upgrade to ${marker.targetVersion} failed and the automatic rollback to ${marker.previousVersion} also failed: ${error instanceof Error ? error.message : error}. Manual intervention needed.`,
      severity: "urgent",
    });
    return;
  }

  log.warn({ to: marker.previousVersion }, "rollback installed; re-executing");
  restarter.restart();
};

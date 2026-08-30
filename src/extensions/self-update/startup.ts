import { NOTIFY_EVENT, type NotifyPayload } from "../../events.ts";
import type { Logger } from "../../log.ts";
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

/** Emit the shared "back online" info notice; callers supply the tail of the text. */
const announceBackOnline = (emit: NotifyEmitter, text: string): void => {
  notify(emit, { title: "Back online", text, severity: "info" });
};

/**
 * Boot-time reconciliation of any markers left by a prior boot.
 *
 *  - No marker → clean boot, nothing to do.
 *  - Restart marker → a plain `restart_self` completed: announce "back online"
 *    and clear the marker. (Same-version, so there is no success/rollback check.)
 *  - Upgrade marker and we're now running the target version → the upgrade
 *    landed: announce "back online", clear the marker AND the loop guard (a
 *    successful run supersedes any earlier failure).
 *  - Upgrade marker but we're NOT on the target → the new version failed to come
 *    up cleanly. Record the target as failed (loop guard), reinstall the previous
 *    version, clear the marker, then re-exec back onto the known-good build. The
 *    "back online" notice is surfaced by the *next* boot's success path.
 *
 * The upgrade marker takes precedence over a restart marker when both are
 * present: the upgrade's outcome supersedes whatever the restart was going to
 * announce, so the restart marker is consumed (cleared) alongside the upgrade
 * reconciliation rather than surviving to fire a spurious notice next boot. The
 * upgrade marker is cleared before any restart so a rollback that itself fails
 * to boot does not loop forever — the next boot sees no marker and stays put.
 */
export const reconcileStartup = async ({
  installer,
  restarter,
  state,
  currentVersion,
  emit,
  log,
}: StartupDeps): Promise<void> => {
  const decision = decideStartup(
    state.getUpgradeMarker(),
    state.getRestartMarker(),
    currentVersion,
  );

  if (decision.action === STARTUP_ACTIONS.none) return;

  if (decision.action === STARTUP_ACTIONS.restartCompleted) {
    log.info("restart completed; clearing marker");

    state.clearRestartMarker();

    announceBackOnline(emit, "Tachikoma is back online after a restart.");

    return;
  }

  // The upgrade marker wins over any restart marker: its outcome supersedes
  // whatever the restart was going to announce, so consume the restart marker
  // now rather than leaving it to fire a spurious notice on the next boot.
  state.clearRestartMarker();

  const marker = decision.marker;

  if (decision.action === STARTUP_ACTIONS.upgradeSucceeded) {
    log.info(
      { from: marker.previousVersion, to: marker.targetVersion },
      "upgrade succeeded; clearing marker",
    );

    state.clearUpgradeMarker();
    state.clearFailedVersion();

    announceBackOnline(
      emit,
      `Tachikoma is back online after upgrading from ${marker.previousVersion} to ${marker.targetVersion}.`,
    );

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

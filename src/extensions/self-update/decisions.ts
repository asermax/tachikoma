import type { LastCheckState, UpgradeMarker } from "./state.ts";
import { isNewerVersion } from "./version.ts";

/**
 * Pure decision functions for the self-update lifecycle. Everything here is a
 * synchronous, side-effect-free transformation over plain data so it can be
 * exhaustively unit-tested; the dangerous I/O (registry, install, restart) is
 * injected as seams by the callers.
 */

export interface CheckInputs {
  currentVersion: string;
  latestVersion: string | null;
  /** Version that previously failed and rolled back; never re-offered until superseded. */
  failedVersion: string | null;
  /** Version the user was already told about, to suppress repeat notifications. */
  notifiedVersion: string | null;
}

export const CHECK_OUTCOMES = {
  registryUnavailable: "registryUnavailable",
  upToDate: "upToDate",
  /** A newer version exists but it is the one that just failed — stay put (loop guard). */
  skippedFailed: "skippedFailed",
  /** A newer version exists and we already notified about exactly this one. */
  alreadyNotified: "alreadyNotified",
  /** A newer version exists and the user should be notified now. */
  notify: "notify",
} as const;

export type CheckOutcome = keyof typeof CHECK_OUTCOMES;

export interface CheckDecision {
  outcome: CheckOutcome;
  latestVersion: string | null;
}

/**
 * Decide whether to notify about a newer published version. The loop guard
 * (`failedVersion`) wins over notification: a version that rolled back is treated
 * as "do not surface" until a strictly newer one appears, so the user is never
 * nudged toward a build we already know is broken on this machine.
 */
export const decideCheck = ({
  currentVersion,
  latestVersion,
  failedVersion,
  notifiedVersion,
}: CheckInputs): CheckDecision => {
  if (latestVersion == null) {
    return { outcome: CHECK_OUTCOMES.registryUnavailable, latestVersion: null };
  }

  if (!isNewerVersion(currentVersion, latestVersion)) {
    return { outcome: CHECK_OUTCOMES.upToDate, latestVersion };
  }

  if (failedVersion != null && latestVersion === failedVersion) {
    return { outcome: CHECK_OUTCOMES.skippedFailed, latestVersion };
  }

  if (notifiedVersion === latestVersion) {
    return { outcome: CHECK_OUTCOMES.alreadyNotified, latestVersion };
  }

  return { outcome: CHECK_OUTCOMES.notify, latestVersion };
};

export const nextLastCheck = (
  decision: CheckDecision,
  previous: LastCheckState | null,
  now: Date,
): LastCheckState => ({
  checkedAt: now.toISOString(),
  latestSeen: decision.latestVersion,
  notifiedVersion:
    decision.outcome === CHECK_OUTCOMES.notify
      ? decision.latestVersion
      : (previous?.notifiedVersion ?? null),
});

/**
 * Decide whether the agent should attempt an upgrade to `latestVersion` now.
 * Refuses when already current or when the target is the known-failed version.
 */
export interface UpgradeGateInputs {
  currentVersion: string;
  latestVersion: string | null;
  failedVersion: string | null;
}

export const UPGRADE_GATES = {
  proceed: "proceed",
  upToDate: "upToDate",
  registryUnavailable: "registryUnavailable",
  blockedFailed: "blockedFailed",
} as const;

export type UpgradeGate = keyof typeof UPGRADE_GATES;

export const decideUpgrade = ({
  currentVersion,
  latestVersion,
  failedVersion,
}: UpgradeGateInputs): UpgradeGate => {
  if (latestVersion == null) return UPGRADE_GATES.registryUnavailable;
  if (!isNewerVersion(currentVersion, latestVersion)) return UPGRADE_GATES.upToDate;
  if (failedVersion != null && latestVersion === failedVersion) return UPGRADE_GATES.blockedFailed;

  return UPGRADE_GATES.proceed;
};

/**
 * Decide what to do on startup given any upgrade marker left by a prior boot.
 *
 *  - no marker                         → nothing (clean boot)
 *  - marker, now running target        → upgrade succeeded; announce + clear
 *  - marker, NOT running target        → the new version failed to take; roll
 *                                        back to the previous version, record the
 *                                        target as failed (loop guard), clear marker
 */
export const STARTUP_ACTIONS = {
  none: "none",
  upgradeSucceeded: "upgradeSucceeded",
  rollback: "rollback",
} as const;

export type StartupAction = keyof typeof STARTUP_ACTIONS;

export interface StartupDecision {
  action: StartupAction;
  marker: UpgradeMarker | null;
}

export const decideStartup = (
  marker: UpgradeMarker | null,
  currentVersion: string,
): StartupDecision => {
  if (marker == null) return { action: STARTUP_ACTIONS.none, marker: null };

  if (currentVersion === marker.targetVersion) {
    return { action: STARTUP_ACTIONS.upgradeSucceeded, marker };
  }

  return { action: STARTUP_ACTIONS.rollback, marker };
};

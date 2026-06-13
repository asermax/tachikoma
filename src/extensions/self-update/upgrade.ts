import type { Logger } from "../../log.ts";
import { decideUpgrade, UPGRADE_GATES } from "./decisions.ts";
import type { DevInstallDetector, Installer, RegistryClient, Restarter } from "./seams.ts";
import type { SelfUpdateState } from "./state.ts";

export interface UpgradeDeps {
  registry: RegistryClient;
  installer: Installer;
  restarter: Restarter;
  devInstall: DevInstallDetector;
  state: SelfUpdateState;
  currentVersion: string;
  log: Logger;
  now?: () => Date;
}

export interface UpgradeOutcome {
  status: "started" | "up-to-date" | "registry-unavailable" | "blocked-failed" | "dev-install";
  detail: string;
}

/**
 * Perform an upgrade to the latest published version:
 *
 *  1. gate on the pure decision (current? known-failed? registry down?),
 *  2. write the in-progress marker BEFORE touching the install, so a crash mid-
 *     upgrade is recoverable on the next boot,
 *  3. install the target globally,
 *  4. re-exec so the new code takes over (this call does not return on success).
 *
 * If the install itself throws, the marker is cleared and the failed version is
 * recorded as the loop guard, since no restart will happen and the old code keeps
 * running. A failure that only manifests *after* restart is caught on next boot
 * by the startup reconciler instead.
 */
export const runUpgrade = async ({
  registry,
  installer,
  restarter,
  devInstall,
  state,
  currentVersion,
  log,
  now,
}: UpgradeDeps): Promise<UpgradeOutcome> => {
  const at = now ?? (() => new Date());

  const latestVersion = await registry.fetchLatest();
  const gate = decideUpgrade({
    currentVersion,
    latestVersion,
    failedVersion: state.getFailedVersion(),
  });

  if (gate === UPGRADE_GATES.registryUnavailable) {
    return { status: "registry-unavailable", detail: "Could not reach the npm registry." };
  }

  if (gate === UPGRADE_GATES.upToDate) {
    return { status: "up-to-date", detail: `Already on the latest version (${currentVersion}).` };
  }

  if (gate === UPGRADE_GATES.blockedFailed) {
    return {
      status: "blocked-failed",
      detail: `Version ${latestVersion} previously failed and rolled back; refusing to retry it until a newer version is published.`,
    };
  }

  const target = latestVersion as string;

  if (await devInstall.isDevInstall()) {
    log.warn({ to: target }, "refusing to self-upgrade from a development install");

    return {
      status: "dev-install",
      detail:
        "Refusing to self-upgrade from a development install (linked or source checkout). Upgrade manually from your working tree instead.",
    };
  }

  state.setUpgradeMarker({
    previousVersion: currentVersion,
    targetVersion: target,
    startedAt: at().toISOString(),
  });
  log.warn({ from: currentVersion, to: target }, "upgrade marker written; installing");

  try {
    await installer.install(target);
  } catch (error) {
    state.clearUpgradeMarker();
    state.setFailedVersion(target);
    log.error({ to: target, err: error }, "install failed; cleared marker and recorded loop guard");

    throw new Error(
      `Install of ${target} failed: ${error instanceof Error ? error.message : error}`,
    );
  }

  log.warn({ to: target }, "install succeeded; re-executing");
  return restarter.restart();
};

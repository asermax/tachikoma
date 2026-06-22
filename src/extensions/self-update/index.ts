import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { defineExtension } from "../api.ts";
import { type NotifyEmitter, runCheck } from "./checker.ts";
import {
  CommandInstaller,
  NpmGlobalDevInstallDetector,
  NpmRegistryClient,
  PACKAGE_NAME,
  ProcessRestarter,
  readInstalledVersion,
} from "./seams.ts";
import { reconcileStartup } from "./startup.ts";
import { SelfUpdateState } from "./state.ts";
import { createRestartToolFactory, createUpgradeToolFactory } from "./tools.ts";
import type { UpgradeDeps } from "./upgrade.ts";
import { SELF_UPDATE_USAGE } from "./usage.ts";

interface SelfUpdateConfig {
  enabled: boolean;
  /** Cron expression for the periodic version check. */
  checkCron: string;
  /**
   * Global install command template; `{version}` is substituted with the target.
   * Defaults to npm; set to a pnpm/bun/uv-equivalent for other setups.
   */
  installCommand: string;
}

const DEFAULT_INSTALL_COMMAND = `npm install -g ${PACKAGE_NAME}@{version}`;

/**
 * Self-update subsystem: periodically checks the npm registry for a newer
 * published version and notifies; exposes an `upgrade_self` tool that installs
 * the latest and re-execs; and on every boot reconciles any in-progress upgrade
 * marker — announcing success or automatically rolling back a failed upgrade.
 *
 * All dangerous operations (registry fetch, global install, process re-exec) sit
 * behind injectable seams; the version-compare, check, upgrade-gate, and
 * rollback decisions are pure and unit-tested.
 */
export default defineExtension<SelfUpdateConfig>({
  name: "self-update",

  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
    // Daily at 10:00 by default — frequent enough to surface releases, not noisy.
    checkCron: Type.String({ default: "0 10 * * *" }),
    installCommand: Type.String({ default: DEFAULT_INSTALL_COMMAND }),
  }),

  async setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("self-update disabled by config");
      return;
    }

    const currentVersion = await readInstalledVersion(app.log);
    const state = new SelfUpdateState(app.state);
    const registry = new NpmRegistryClient(app.log);
    const installer = new CommandInstaller(app.extensionConfig.installCommand, app.log);
    const restarter = new ProcessRestarter(app.log);
    const devInstall = new NpmGlobalDevInstallDetector(app.log);
    const emit: NotifyEmitter = (event, payload) => app.events.emit(event, payload);

    app.log.info({ currentVersion }, "self-update active");

    app.bootstrap("reconcile", () =>
      reconcileStartup({ installer, restarter, state, currentVersion, emit, log: app.log }),
    );

    const upgradeDeps = (): UpgradeDeps => ({
      registry,
      installer,
      devInstall,
      state,
      currentVersion,
      log: app.log,
    });

    app.agent.use(createUpgradeToolFactory(upgradeDeps, () => restarter, app.requestRestart));
    app.agent.use(createRestartToolFactory(() => restarter, app.requestRestart, app.log));

    app.agent.use(provideContext(SELF_UPDATE_USAGE, "self-update-usage"));

    app.scheduler.cron("self-update-check", app.extensionConfig.checkCron, () =>
      runCheck({ registry, state, currentVersion, emit, log: app.log }),
    );
  },
});

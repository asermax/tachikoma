import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { Logger } from "../../log.ts";
import type { Restarter } from "./seams.ts";
import { runUpgrade, type UpgradeDeps } from "./upgrade.ts";

export const UpgradeSelfParams = Type.Object({});

export const RestartSelfParams = Type.Object({});

/**
 * pi extension factory exposing the `upgrade_self` tool. On a successful upgrade
 * the process re-execs, so the tool call does not return; the agent should treat
 * a returned string as "did not restart" (already current, blocked, or registry
 * down).
 */
export const createUpgradeToolFactory =
  (deps: () => UpgradeDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "upgrade_self",
      label: "Upgrade Tachikoma",
      description:
        "Upgrade Tachikoma to the latest published version, then restart to load it. If the new version fails to boot, the previous version is automatically restored. Returns a message only when no restart happened (already up to date, registry unreachable, or the target previously failed).",
      promptSnippet: "Upgrade Tachikoma to the latest version (restarts the process)",
      promptGuidelines: [
        "Only call upgrade_self when the user explicitly asks to update/upgrade Tachikoma.",
        "A successful upgrade restarts the process, so you will not see a tool result; tell the user the upgrade is starting before calling it.",
      ],
      parameters: UpgradeSelfParams,
      async execute() {
        const resolved = deps();
        resolved.log.info({ current: resolved.currentVersion }, "upgrade_self invoked");

        const outcome = await runUpgrade(resolved);

        return {
          content: [{ type: "text", text: outcome.detail }],
          details: undefined,
        };
      },
    });
  };

/**
 * pi extension factory exposing the `restart_self` tool. Restarts the process
 * via the Restarter seam WITHOUT upgrading, so the call does not return on
 * success; the agent should treat any returned text as "did not restart".
 */
export const createRestartToolFactory =
  (restarter: () => Restarter, log: Logger): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "restart_self",
      label: "Restart Tachikoma",
      description:
        "Restart Tachikoma without upgrading, to pick up configuration or state changes. This re-execs the current version in place; on success the process restarts and the tool call does not return.",
      promptSnippet: "Restart Tachikoma in place to pick up config/state changes (no upgrade)",
      promptGuidelines: [
        "Only call restart_self when the user explicitly asks to restart Tachikoma without upgrading (e.g. to apply config or state changes).",
        "A successful restart re-execs the process, so you will not see a tool result; tell the user the restart is starting before calling it.",
      ],
      parameters: RestartSelfParams,
      async execute() {
        log.info("restart_self invoked");

        return restarter().restart();
      },
    });
  };

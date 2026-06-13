import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import { runUpgrade, type UpgradeDeps } from "./upgrade.ts";

export const UpgradeSelfParams = Type.Object({});

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
        const outcome = await runUpgrade(deps());

        return {
          content: [{ type: "text", text: outcome.detail }],
          details: undefined,
        };
      },
    });
  };

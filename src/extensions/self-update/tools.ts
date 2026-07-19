import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { Logger } from "../../log.ts";
import type { Restarter } from "./seams.ts";
import type { SelfUpdateState } from "./state.ts";
import { runUpgrade, type UpgradeDeps } from "./upgrade.ts";

export const UpgradeSelfParams = Type.Object({});

export const RestartSelfParams = Type.Object({});

/**
 * A deferred-restart sink: the tool hands its restarter thunk here instead of re-execing
 * directly, so the current exchange (the tool result + the agent's turn) completes before
 * the process restarts. In production this is `app.requestRestart` → the coordinator, which
 * drains then lets `app.ts` perform the re-exec.
 */
export type RequestRestart = (restart: () => never) => void;

/**
 * pi Extension factory exposing the `upgrade_self` tool. On a successful upgrade the tool
 * schedules a deferred restart and returns a `started` message; the process re-execs only
 * after the current exchange completes, so the agent always sees a tool result and can
 * finish its turn. A returned message for any other outcome (already current, registry
 * unreachable, previously failed, dev install) means no restart was scheduled.
 */
export const createUpgradeToolFactory =
  (
    deps: () => UpgradeDeps,
    restarter: () => Restarter,
    requestRestart: RequestRestart,
  ): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "upgrade_self",
      label: "Upgrade Tachikoma",
      description:
        "Upgrade Tachikoma to the latest published version, then restart to load it. The restart runs after this exchange finishes so your response is delivered in full; if the new version fails to boot, the previous version is automatically restored. Returns a message for every outcome (started, already up to date, registry unreachable, or the target previously failed).",
      promptSnippet: "Upgrade Tachikoma to the latest version (restarts after this exchange)",
      promptGuidelines: [
        "Only call upgrade_self when the user explicitly asks to update/upgrade Tachikoma.",
        "The restart is deferred until after this exchange, so you will see a tool result — tell the user the upgrade is starting, then finish your turn; the process restarts once the exchange completes.",
      ],
      parameters: UpgradeSelfParams,
      async execute() {
        const resolved = deps();
        resolved.log.info({ current: resolved.currentVersion }, "upgrade_self invoked");

        const outcome = await runUpgrade(resolved);

        // The install committed — schedule a deferred restart so the current exchange (this
        // tool result + the agent's turn) completes before the process re-execs.
        if (outcome.status === "started") {
          requestRestart(() => restarter().restart());
        }

        return {
          content: [{ type: "text", text: outcome.detail }],
          details: undefined,
        };
      },
    });
  };

/**
 * pi Extension factory exposing the `restart_self` tool. Writes a restart marker, schedules
 * a deferred restart via the Restarter seam WITHOUT upgrading, and returns a message; the
 * process re-execs only after the current exchange completes, so the agent always sees a
 * tool result and can finish its turn. The marker is consumed on the next boot to emit the
 * post-restart "back online" notification.
 */
export const createRestartToolFactory =
  (
    restarter: () => Restarter,
    requestRestart: RequestRestart,
    state: SelfUpdateState,
    log: Logger,
  ): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "restart_self",
      label: "Restart Tachikoma",
      description:
        "Restart Tachikoma without upgrading, to pick up configuration or state changes. This re-execs the current version in place; the restart runs after this exchange finishes so your response is delivered in full.",
      promptSnippet:
        "Restart Tachikoma in place to pick up config/state changes (restarts after this exchange, no upgrade)",
      promptGuidelines: [
        "Only call restart_self when the user explicitly asks to restart Tachikoma without upgrading (e.g. to apply config or state changes).",
        "The restart is deferred until after this exchange, so you will see a tool result — tell the user the restart is starting, then finish your turn; the process restarts once the exchange completes.",
      ],
      parameters: RestartSelfParams,
      async execute() {
        log.info("restart_self invoked");

        // Write the restart marker BEFORE scheduling the deferred re-exec (mirrors runUpgrade):
        // the KV write is synchronous, so the marker is on disk before the process restarts,
        // and the next boot announces "back online".
        state.setRestartMarker({ startedAt: new Date().toISOString() });

        // Schedule a deferred restart so the current exchange (this tool result + the agent's
        // turn) completes before the process re-execs.
        requestRestart(() => restarter().restart());

        return {
          content: [{ type: "text", text: "Restarting Tachikoma now to apply the changes." }],
          details: undefined,
        };
      },
    });
  };

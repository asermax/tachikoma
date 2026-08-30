import { NOTIFY_EVENT, type NotifyPayload } from "../../events.ts";
import type { Logger } from "../../log.ts";
import { CHECK_OUTCOMES, decideCheck, nextLastCheck } from "./decisions.ts";
import type { RegistryClient } from "./seams.ts";
import type { SelfUpdateState } from "./state.ts";

export type NotifyEmitter = (event: string, payload: NotifyPayload) => void;

export interface CheckerDeps {
  registry: RegistryClient;
  state: SelfUpdateState;
  currentVersion: string;
  emit: NotifyEmitter;
  log: Logger;
  now?: () => Date;
}

/**
 * One scheduled check tick: ask the registry for the latest version, run the
 * pure decision, persist the bookkeeping, and emit a `notify` event when (and
 * only when) a genuinely newer, non-failed, not-yet-announced version exists.
 */
export const runCheck = async ({
  registry,
  state,
  currentVersion,
  emit,
  log,
  now,
}: CheckerDeps): Promise<void> => {
  const at = now ?? (() => new Date());

  const latestVersion = await registry.fetchLatest();

  const decision = decideCheck({
    currentVersion,
    latestVersion,
    failedVersion: state.getFailedVersion(),
    notifiedVersion: state.getLastCheck()?.notifiedVersion ?? null,
  });

  log.info(
    { current: currentVersion, latest: latestVersion, outcome: decision.outcome },
    "self-update check",
  );

  if (decision.outcome === CHECK_OUTCOMES.notify && decision.latestVersion != null) {
    log.info(
      { current: currentVersion, latest: decision.latestVersion },
      "emitting update-available notification",
    );

    emit(NOTIFY_EVENT, {
      title: "Update available",
      text: `A new version of tachikoma is available: ${currentVersion} → ${decision.latestVersion}. Ask me to upgrade when you're ready.`,
      severity: "info",
      source: "self-update",
    });
  }

  // Registry failures leave bookkeeping untouched — we only learned nothing.
  if (decision.outcome !== CHECK_OUTCOMES.registryUnavailable) {
    state.setLastCheck(nextLastCheck(decision, state.getLastCheck(), at()));
  }
};

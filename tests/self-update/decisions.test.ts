import { describe, expect, it } from "vitest";

import {
  CHECK_OUTCOMES,
  decideCheck,
  decideStartup,
  decideUpgrade,
  nextLastCheck,
  STARTUP_ACTIONS,
  UPGRADE_GATES,
} from "../../src/extensions/self-update/decisions.ts";
import type { UpgradeMarker } from "../../src/extensions/self-update/state.ts";

const CURRENT = "2.0.1";

describe("decideCheck", () => {
  it("reports registryUnavailable when latest is null", () => {
    const decision = decideCheck({
      currentVersion: CURRENT,
      latestVersion: null,
      failedVersion: null,
      notifiedVersion: null,
    });

    expect(decision.outcome).toBe(CHECK_OUTCOMES.registryUnavailable);
  });

  it("reports upToDate when latest is not newer", () => {
    expect(
      decideCheck({
        currentVersion: CURRENT,
        latestVersion: "2.0.1",
        failedVersion: null,
        notifiedVersion: null,
      }).outcome,
    ).toBe(CHECK_OUTCOMES.upToDate);
  });

  it("notifies when a newer, unseen, non-failed version exists", () => {
    expect(
      decideCheck({
        currentVersion: CURRENT,
        latestVersion: "2.1.0",
        failedVersion: null,
        notifiedVersion: null,
      }).outcome,
    ).toBe(CHECK_OUTCOMES.notify);
  });

  it("loop-guards: skips a newer version that previously failed", () => {
    expect(
      decideCheck({
        currentVersion: CURRENT,
        latestVersion: "2.1.0",
        failedVersion: "2.1.0",
        notifiedVersion: null,
      }).outcome,
    ).toBe(CHECK_OUTCOMES.skippedFailed);
  });

  it("notifies again once a version newer than the failed one appears", () => {
    expect(
      decideCheck({
        currentVersion: CURRENT,
        latestVersion: "2.2.0",
        failedVersion: "2.1.0",
        notifiedVersion: null,
      }).outcome,
    ).toBe(CHECK_OUTCOMES.notify);
  });

  it("does not re-notify the same version twice", () => {
    expect(
      decideCheck({
        currentVersion: CURRENT,
        latestVersion: "2.1.0",
        failedVersion: null,
        notifiedVersion: "2.1.0",
      }).outcome,
    ).toBe(CHECK_OUTCOMES.alreadyNotified);
  });
});

describe("nextLastCheck", () => {
  const now = new Date("2026-06-13T10:00:00Z");

  it("records the notified version only on a notify outcome", () => {
    const decision = decideCheck({
      currentVersion: CURRENT,
      latestVersion: "2.1.0",
      failedVersion: null,
      notifiedVersion: null,
    });

    expect(nextLastCheck(decision, null, now)).toEqual({
      checkedAt: now.toISOString(),
      latestSeen: "2.1.0",
      notifiedVersion: "2.1.0",
    });
  });

  it("preserves the prior notifiedVersion on a non-notify outcome", () => {
    const decision = decideCheck({
      currentVersion: CURRENT,
      latestVersion: "2.0.1",
      failedVersion: null,
      notifiedVersion: "2.1.0",
    });

    expect(
      nextLastCheck(
        decision,
        { checkedAt: "old", latestSeen: "2.1.0", notifiedVersion: "2.1.0" },
        now,
      ).notifiedVersion,
    ).toBe("2.1.0");
  });
});

describe("decideUpgrade", () => {
  it("proceeds toward a newer, non-failed version", () => {
    expect(
      decideUpgrade({ currentVersion: CURRENT, latestVersion: "2.1.0", failedVersion: null }),
    ).toBe(UPGRADE_GATES.proceed);
  });

  it("refuses when already current", () => {
    expect(
      decideUpgrade({ currentVersion: CURRENT, latestVersion: "2.0.1", failedVersion: null }),
    ).toBe(UPGRADE_GATES.upToDate);
  });

  it("refuses when the registry is unavailable", () => {
    expect(
      decideUpgrade({ currentVersion: CURRENT, latestVersion: null, failedVersion: null }),
    ).toBe(UPGRADE_GATES.registryUnavailable);
  });

  it("loop-guards a known-failed target", () => {
    expect(
      decideUpgrade({ currentVersion: CURRENT, latestVersion: "2.1.0", failedVersion: "2.1.0" }),
    ).toBe(UPGRADE_GATES.blockedFailed);
  });
});

describe("decideStartup", () => {
  const marker: UpgradeMarker = {
    previousVersion: "2.0.1",
    targetVersion: "2.1.0",
    startedAt: "2026-06-13T10:00:00Z",
  };

  it("does nothing on a clean boot", () => {
    expect(decideStartup(null, CURRENT).action).toBe(STARTUP_ACTIONS.none);
  });

  it("recognises a successful upgrade when running the target", () => {
    expect(decideStartup(marker, "2.1.0").action).toBe(STARTUP_ACTIONS.upgradeSucceeded);
  });

  it("calls for rollback when not running the target", () => {
    expect(decideStartup(marker, "2.0.1").action).toBe(STARTUP_ACTIONS.rollback);
  });
});

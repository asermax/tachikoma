import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NOTIFY_EVENT } from "../../src/extensions/notifications/payload.ts";
import { runCheck } from "../../src/extensions/self-update/checker.ts";
import type {
  DevInstallDetector,
  Installer,
  RegistryClient,
  Restarter,
} from "../../src/extensions/self-update/seams.ts";
import { reconcileStartup } from "../../src/extensions/self-update/startup.ts";
import { SelfUpdateState } from "../../src/extensions/self-update/state.ts";
import { createRestartToolFactory } from "../../src/extensions/self-update/tools.ts";
import { runUpgrade } from "../../src/extensions/self-update/upgrade.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

const CURRENT = "2.0.1";
const now = () => new Date("2026-06-13T10:00:00Z");

const createState = () => {
  const store = new Map<string, unknown>();

  return new SelfUpdateState({
    get: <T>(key: string): T | null => (store.has(key) ? (store.get(key) as T) : null),
    set: <T>(key: string, value: T): void => {
      store.set(key, value);
    },
    delete: (key: string): void => {
      store.delete(key);
    },
  });
};

const registryReturning = (version: string | null): RegistryClient => ({
  fetchLatest: async () => version,
});

const devInstallReturning = (isDev: boolean): DevInstallDetector => ({
  isDevInstall: async () => isDev,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("runCheck", () => {
  it("emits a notify event when a newer version exists", async () => {
    const state = createState();
    const emit = vi.fn();

    await runCheck({
      registry: registryReturning("2.1.0"),
      state,
      currentVersion: CURRENT,
      emit,
      log: fakeLog,
      now,
    });

    expect(emit).toHaveBeenCalledOnce();
    const [event, payload] = emit.mock.calls[0] as [string, { text: string; source: string }];
    expect(event).toBe(NOTIFY_EVENT);
    expect(payload.source).toBe("self-update");
    expect(payload.text).toContain("2.1.0");
    expect(state.getLastCheck()?.notifiedVersion).toBe("2.1.0");
  });

  it("is a no-op notification when already current", async () => {
    const state = createState();
    const emit = vi.fn();

    await runCheck({
      registry: registryReturning("2.0.1"),
      state,
      currentVersion: CURRENT,
      emit,
      log: fakeLog,
      now,
    });

    expect(emit).not.toHaveBeenCalled();
    expect(state.getLastCheck()?.latestSeen).toBe("2.0.1");
  });

  it("does not notify twice for the same version", async () => {
    const state = createState();
    const emit = vi.fn();
    const deps = {
      registry: registryReturning("2.1.0"),
      state,
      currentVersion: CURRENT,
      emit,
      log: fakeLog,
      now,
    };

    await runCheck(deps);
    await runCheck(deps);

    expect(emit).toHaveBeenCalledOnce();
  });

  it("does not notify for a version that previously failed (loop guard)", async () => {
    const state = createState();
    state.setFailedVersion("2.1.0");
    const emit = vi.fn();

    await runCheck({
      registry: registryReturning("2.1.0"),
      state,
      currentVersion: CURRENT,
      emit,
      log: fakeLog,
      now,
    });

    expect(emit).not.toHaveBeenCalled();
  });

  it("leaves bookkeeping untouched when the registry is unavailable", async () => {
    const state = createState();
    const emit = vi.fn();

    await runCheck({
      registry: registryReturning(null),
      state,
      currentVersion: CURRENT,
      emit,
      log: fakeLog,
      now,
    });

    expect(emit).not.toHaveBeenCalled();
    expect(state.getLastCheck()).toBeNull();
  });
});

describe("runUpgrade", () => {
  const createRestarter = () => {
    const restart = vi.fn(() => {
      throw new Error("__restart__");
    });
    return { restart } as unknown as Restarter & { restart: ReturnType<typeof vi.fn> };
  };

  it("writes a marker, installs the target, then restarts", async () => {
    const state = createState();
    const install = vi.fn(async () => {});
    const installer: Installer = { install };
    const restarter = createRestarter();

    await expect(
      runUpgrade({
        registry: registryReturning("2.1.0"),
        installer,
        restarter,
        devInstall: devInstallReturning(false),
        state,
        currentVersion: CURRENT,
        log: fakeLog,
        now,
      }),
    ).rejects.toThrow("__restart__");

    expect(state.getUpgradeMarker()).toMatchObject({
      previousVersion: CURRENT,
      targetVersion: "2.1.0",
    });
    expect(install).toHaveBeenCalledWith("2.1.0");
    expect(restarter.restart).toHaveBeenCalledOnce();
  });

  it("returns up-to-date without touching the installer or marker", async () => {
    const state = createState();
    const install = vi.fn(async () => {});
    const restarter = createRestarter();

    const outcome = await runUpgrade({
      registry: registryReturning("2.0.1"),
      installer: { install },
      restarter,
      devInstall: devInstallReturning(false),
      state,
      currentVersion: CURRENT,
      log: fakeLog,
      now,
    });

    expect(outcome.status).toBe("up-to-date");
    expect(install).not.toHaveBeenCalled();
    expect(state.getUpgradeMarker()).toBeNull();
    expect(restarter.restart).not.toHaveBeenCalled();
  });

  it("refuses to retry a known-failed version", async () => {
    const state = createState();
    state.setFailedVersion("2.1.0");
    const install = vi.fn(async () => {});

    const outcome = await runUpgrade({
      registry: registryReturning("2.1.0"),
      installer: { install },
      restarter: createRestarter(),
      devInstall: devInstallReturning(false),
      state,
      currentVersion: CURRENT,
      log: fakeLog,
      now,
    });

    expect(outcome.status).toBe("blocked-failed");
    expect(install).not.toHaveBeenCalled();
  });

  it("clears the marker and records the loop guard when the install fails", async () => {
    const state = createState();
    const install = vi.fn(async () => {
      throw new Error("network down");
    });
    const restarter = createRestarter();

    await expect(
      runUpgrade({
        registry: registryReturning("2.1.0"),
        installer: { install },
        restarter,
        devInstall: devInstallReturning(false),
        state,
        currentVersion: CURRENT,
        log: fakeLog,
        now,
      }),
    ).rejects.toThrow(/Install of 2.1.0 failed/);

    expect(state.getUpgradeMarker()).toBeNull();
    expect(state.getFailedVersion()).toBe("2.1.0");
    expect(restarter.restart).not.toHaveBeenCalled();
  });

  it("refuses to install from a development install and leaves state untouched", async () => {
    const state = createState();
    const install = vi.fn(async () => {});
    const restarter = createRestarter();

    const outcome = await runUpgrade({
      registry: registryReturning("2.1.0"),
      installer: { install },
      restarter,
      devInstall: devInstallReturning(true),
      state,
      currentVersion: CURRENT,
      log: fakeLog,
      now,
    });

    expect(outcome.status).toBe("dev-install");
    expect(install).not.toHaveBeenCalled();
    expect(state.getUpgradeMarker()).toBeNull();
    expect(restarter.restart).not.toHaveBeenCalled();
  });

  it("proceeds with the install when not a development install", async () => {
    const state = createState();
    const install = vi.fn(async () => {});
    const restarter = createRestarter();

    await expect(
      runUpgrade({
        registry: registryReturning("2.1.0"),
        installer: { install },
        restarter,
        devInstall: devInstallReturning(false),
        state,
        currentVersion: CURRENT,
        log: fakeLog,
        now,
      }),
    ).rejects.toThrow("__restart__");

    expect(install).toHaveBeenCalledWith("2.1.0");
    expect(restarter.restart).toHaveBeenCalledOnce();
  });
});

describe("createRestartToolFactory", () => {
  interface CapturedTool {
    name: string;
    execute: () => Promise<unknown>;
  }

  it("registers restart_self and restarts via the Restarter seam on execute", async () => {
    const restart = vi.fn(() => {
      throw new Error("__restart__");
    });
    const restarter = { restart } as unknown as Restarter;

    let captured: CapturedTool | null = null;
    const pi = { registerTool: (tool: CapturedTool) => (captured = tool) };

    createRestartToolFactory(() => restarter)(pi as unknown as Parameters<ExtensionFactory>[0]);

    expect(captured).not.toBeNull();
    const tool = captured as unknown as CapturedTool;
    expect(tool.name).toBe("restart_self");

    await expect(tool.execute()).rejects.toThrow("__restart__");
    expect(restart).toHaveBeenCalledOnce();
  });
});

describe("reconcileStartup", () => {
  const noInstall: Installer = { install: vi.fn(async () => {}) };
  const createRestarter = () => {
    const restart = vi.fn(() => {
      throw new Error("__restart__");
    });
    return { restart } as unknown as Restarter & { restart: ReturnType<typeof vi.fn> };
  };

  it("does nothing on a clean boot", async () => {
    const state = createState();
    const emit = vi.fn();

    await reconcileStartup({
      installer: noInstall,
      restarter: createRestarter(),
      state,
      currentVersion: CURRENT,
      emit,
      log: fakeLog,
    });

    expect(emit).not.toHaveBeenCalled();
  });

  it("announces success and clears markers when running the target", async () => {
    const state = createState();
    state.setUpgradeMarker({
      previousVersion: "2.0.1",
      targetVersion: "2.1.0",
      startedAt: now().toISOString(),
    });
    state.setFailedVersion("1.9.0");
    const emit = vi.fn();

    await reconcileStartup({
      installer: noInstall,
      restarter: createRestarter(),
      state,
      currentVersion: "2.1.0",
      emit,
      log: fakeLog,
    });

    expect(emit).toHaveBeenCalledOnce();
    const [, payload] = emit.mock.calls[0] as [string, { text: string }];
    expect(payload.text).toContain("back online");
    expect(state.getUpgradeMarker()).toBeNull();
    expect(state.getFailedVersion()).toBeNull();
  });

  it("rolls back, records the failed version, and restarts when target did not boot", async () => {
    const state = createState();
    state.setUpgradeMarker({
      previousVersion: "2.0.1",
      targetVersion: "2.1.0",
      startedAt: now().toISOString(),
    });
    const install = vi.fn(async () => {});
    const restarter = createRestarter();
    const emit = vi.fn();

    await expect(
      reconcileStartup({
        installer: { install },
        restarter,
        state,
        currentVersion: "2.0.1",
        emit,
        log: fakeLog,
      }),
    ).rejects.toThrow("__restart__");

    expect(install).toHaveBeenCalledWith("2.0.1");
    expect(state.getFailedVersion()).toBe("2.1.0");
    expect(state.getUpgradeMarker()).toBeNull();
    expect(restarter.restart).toHaveBeenCalledOnce();
  });

  it("emits an urgent notice and does not restart when rollback install fails", async () => {
    const state = createState();
    state.setUpgradeMarker({
      previousVersion: "2.0.1",
      targetVersion: "2.1.0",
      startedAt: now().toISOString(),
    });
    const install = vi.fn(async () => {
      throw new Error("registry down");
    });
    const restarter = createRestarter();
    const emit = vi.fn();

    await reconcileStartup({
      installer: { install },
      restarter,
      state,
      currentVersion: "2.0.1",
      emit,
      log: fakeLog,
    });

    expect(restarter.restart).not.toHaveBeenCalled();
    const [, payload] = emit.mock.calls[0] as [string, { severity: string }];
    expect(payload.severity).toBe("urgent");
    expect(state.getFailedVersion()).toBe("2.1.0");
  });
});

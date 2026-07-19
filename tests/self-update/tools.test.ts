import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import type { Restarter } from "../../src/extensions/self-update/seams.ts";
import { SelfUpdateState } from "../../src/extensions/self-update/state.ts";
import {
  createRestartToolFactory,
  createUpgradeToolFactory,
} from "../../src/extensions/self-update/tools.ts";
import type { UpgradeDeps } from "../../src/extensions/self-update/upgrade.ts";
import type { Logger } from "../../src/log.ts";

const runUpgradeMock = vi.hoisted(() => vi.fn());

const createFakeLog = () => {
  const log = {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

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

vi.mock("../../src/extensions/self-update/upgrade.ts", () => ({
  runUpgrade: runUpgradeMock,
}));

interface RegisteredTool {
  name: string;
  execute: (toolCallId: string, params: unknown) => Promise<{ content: { text: string }[] }>;
}

const registerInto = (factory: ExtensionFactory) => {
  const tools: RegisteredTool[] = [];
  const pi = { registerTool: (tool: RegisteredTool) => tools.push(tool) };

  factory(pi as unknown as Parameters<ExtensionFactory>[0]);

  return tools;
};

describe("createUpgradeToolFactory", () => {
  it("registers upgrade_self and returns the outcome detail without restarting on a non-started outcome", async () => {
    runUpgradeMock.mockResolvedValue({ detail: "already up to date" });

    const restarter = vi.fn((): Restarter => ({ restart: vi.fn() }));
    const requestRestart = vi.fn();
    const deps = vi.fn(() => ({ log: createFakeLog() }) as unknown as UpgradeDeps);
    const tools = registerInto(createUpgradeToolFactory(deps, restarter, requestRestart));

    expect(tools.map((tool) => tool.name)).toEqual(["upgrade_self"]);

    const result = await tools[0]?.execute("call-1", {});

    expect(deps).toHaveBeenCalledOnce();
    expect(runUpgradeMock).toHaveBeenCalledOnce();
    expect(result?.content[0]?.text).toBe("already up to date");
    // No restart scheduled for a non-started outcome.
    expect(requestRestart).not.toHaveBeenCalled();
  });

  it("schedules a deferred restart when runUpgrade returns a started outcome", async () => {
    runUpgradeMock.mockResolvedValue({ status: "started", detail: "upgraded; restarting" });

    const restarter = vi.fn((): Restarter => ({ restart: vi.fn() }));
    const requestRestart = vi.fn();
    const deps = vi.fn(() => ({ log: createFakeLog() }) as unknown as UpgradeDeps);
    const tools = registerInto(createUpgradeToolFactory(deps, restarter, requestRestart));

    await tools[0]?.execute("call-1", {});

    // The deferred restart is scheduled; the restarter itself is not touched during the exchange.
    expect(requestRestart).toHaveBeenCalledOnce();
    expect(restarter).not.toHaveBeenCalled();
  });
});

describe("createRestartToolFactory", () => {
  it("registers restart_self, writes a restart marker, and schedules a deferred restart", async () => {
    const restart = vi.fn(() => "unreachable" as never);
    const restarter = vi.fn((): Restarter => ({ restart }));
    const requestRestart = vi.fn();
    const state = createState();

    const tools = registerInto(
      createRestartToolFactory(restarter, requestRestart, state, createFakeLog()),
    );

    expect(tools.map((tool) => tool.name)).toEqual(["restart_self"]);

    const result = await tools[0]?.execute("call-1", {});

    // The restart marker is written synchronously during the exchange, before the
    // deferred re-exec, so the next boot can announce "back online".
    expect(state.getRestartMarker()).not.toBeNull();
    expect(state.getRestartMarker()?.startedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    // The restarter is not invoked during the exchange — only handed to requestRestart.
    expect(restarter).not.toHaveBeenCalled();
    expect(restart).not.toHaveBeenCalled();
    expect(requestRestart).toHaveBeenCalledOnce();
    expect(typeof requestRestart.mock.calls[0]?.[0]).toBe("function");

    // Invoking the scheduled thunk reaches the restarter (the deferred re-exec).
    const scheduled = requestRestart.mock.calls[0]?.[0] as () => never;
    scheduled();
    expect(restarter).toHaveBeenCalledOnce();
    expect(restart).toHaveBeenCalledOnce();

    // The tool returned a result the agent can relay.
    expect(result?.content[0]?.text).toBe("Restarting Tachikoma now to apply the changes.");
  });
});

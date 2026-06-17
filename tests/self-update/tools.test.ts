import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import type { Restarter } from "../../src/extensions/self-update/seams.ts";
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
  it("registers upgrade_self and returns the upgrade outcome detail", async () => {
    runUpgradeMock.mockResolvedValue({ detail: "already up to date" });

    const deps = vi.fn(() => ({ log: createFakeLog() }) as unknown as UpgradeDeps);
    const tools = registerInto(createUpgradeToolFactory(deps));

    expect(tools.map((tool) => tool.name)).toEqual(["upgrade_self"]);

    const result = await tools[0]?.execute("call-1", {});

    expect(deps).toHaveBeenCalledOnce();
    expect(runUpgradeMock).toHaveBeenCalledOnce();
    expect(result?.content[0]?.text).toBe("already up to date");
  });
});

describe("createRestartToolFactory", () => {
  it("registers restart_self and restarts in place through the seam", async () => {
    const restart = vi.fn(() => "unreachable" as never);
    const restarter = vi.fn((): Restarter => ({ restart }));

    const tools = registerInto(createRestartToolFactory(restarter, createFakeLog()));

    expect(tools.map((tool) => tool.name)).toEqual(["restart_self"]);

    await tools[0]?.execute("call-1", {});

    expect(restarter).toHaveBeenCalledOnce();
    expect(restart).toHaveBeenCalledOnce();
  });
});

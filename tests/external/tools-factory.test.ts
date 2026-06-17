import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import type { InstallManager, InstallRecord } from "../../src/extensions/external/installs.ts";
import {
  createExternalToolsFactory,
  handleListInstalledPlugins,
  handleUpdateExternalExtension,
} from "../../src/extensions/external/tools.ts";
import type { Logger } from "../../src/log.ts";

const createFakeLog = (): Logger => {
  const log = { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() };

  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

interface CapturedTool {
  name: string;
  execute: (toolCallId: string, params: unknown) => Promise<{ content: { text: string }[] }>;
}

const captureTools = (manager: InstallManager): CapturedTool[] => {
  const tools: CapturedTool[] = [];
  const pi = { registerTool: (tool: CapturedTool) => tools.push(tool) };

  createExternalToolsFactory(
    manager,
    createFakeLog(),
  )(pi as unknown as Parameters<ExtensionFactory>[0]);

  return tools;
};

const toolByName = (tools: CapturedTool[], name: string): CapturedTool => {
  const tool = tools.find((candidate) => candidate.name === name);

  if (tool == null) throw new Error(`tool '${name}' not registered`);

  return tool;
};

describe("createExternalToolsFactory", () => {
  it("registers the four extension management tools", () => {
    const tools = captureTools({} as InstallManager);

    expect(tools.map((tool) => tool.name)).toEqual([
      "install_extension",
      "update_extension",
      "list_installed_extensions",
      "uninstall_extension",
    ]);
  });

  it("install_extension delegates to the manager and renders a text result", async () => {
    const record: InstallRecord = {
      source: "https://example.com/repo.git",
      path: "/data/ext/demo",
      installedAt: "2026-06-12T10:00:00.000Z",
    };
    const manager = { install: vi.fn().mockResolvedValue(record) } as unknown as InstallManager;

    const tools = captureTools(manager);
    const result = await toolByName(tools, "install_extension").execute("call-1", {
      source: record.source,
      alias: "demo",
    });

    expect(manager.install).toHaveBeenCalledWith(record.source, "demo");
    expect(result.content[0].text).toContain("ExternalExtension 'demo' installed successfully.");
    expect(result.content[0].text).toContain("/data/ext/demo");
  });

  it("update_extension delegates to the manager", async () => {
    const manager = {
      update: vi.fn().mockResolvedValue({ status: "updated", detail: "pulled abc123" }),
    } as unknown as InstallManager;

    const tools = captureTools(manager);
    const result = await toolByName(tools, "update_extension").execute("call-2", { alias: "demo" });

    expect(manager.update).toHaveBeenCalledWith("demo");
    expect(result.content[0].text).toContain("ExternalExtension 'demo' updated.");
    expect(result.content[0].text).toContain("pulled abc123");
  });

  it("list_installed_extensions renders the manager listing", async () => {
    const manager = { list: vi.fn().mockReturnValue({}) } as unknown as InstallManager;

    const tools = captureTools(manager);
    const result = await toolByName(tools, "list_installed_extensions").execute("call-3", {});

    expect(result.content[0].text).toBe("No external extensions installed.");
  });

  it("uninstall_extension delegates to the manager", async () => {
    const manager = {
      uninstall: vi.fn().mockResolvedValue(undefined),
    } as unknown as InstallManager;

    const tools = captureTools(manager);
    const result = await toolByName(tools, "uninstall_extension").execute("call-4", {
      alias: "demo",
    });

    expect(manager.uninstall).toHaveBeenCalledWith("demo");
    expect(result.content[0].text).toContain("ExternalExtension 'demo' uninstalled.");
  });
});

describe("handleUpdateExternalExtension", () => {
  it("reports the skipped detail when the manager skips the update", async () => {
    const manager = {
      update: vi
        .fn()
        .mockResolvedValue({ status: "skipped", detail: "local installs are current" }),
    } as unknown as InstallManager;

    const message = await handleUpdateExternalExtension(manager, { alias: "local-demo" });

    expect(message).toBe("ExternalExtension 'local-demo' skipped: local installs are current");
  });
});

describe("handleListInstalledPlugins", () => {
  it("labels git and local sources distinctly in the listing", () => {
    const manager = {
      list: vi.fn().mockReturnValue({
        demo: {
          source: "https://example.com/repo.git",
          path: "/data/ext/demo",
          installedAt: "2026-06-12T10:00:00.000Z",
        },
        local: {
          source: "/home/user/my-extension",
          path: "/home/user/my-extension",
          installedAt: "2026-06-12T11:00:00.000Z",
        },
      }),
    } as unknown as InstallManager;

    const message = handleListInstalledPlugins(manager);

    expect(message).toContain("**demo** (git)");
    expect(message).toContain("Source: https://example.com/repo.git");
    expect(message).toContain("Path: /data/ext/demo");
    expect(message).toContain("**local** (local)");
    expect(message).toContain("Source: /home/user/my-extension");
  });
});

import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import type { ExtensionAPI, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import type { AppContext, UseFactoryOptions } from "../../src/extensions/api.ts";
import skills from "../../src/extensions/skills/index.ts";

const repoSkillsDir = resolve(import.meta.dirname, "../../src/extensions/skills/builtin-skills");

const setup = async (
  config: { enabled: boolean } = { enabled: true },
): Promise<{
  workspaceDir: string;
  workspaceSkillsDir: string;
  on: ReturnType<typeof vi.fn>;
  registerTool: ReturnType<typeof vi.fn>;
  useOptions: UseFactoryOptions | undefined;
  log: {
    info: ReturnType<typeof vi.fn>;
    warn: ReturnType<typeof vi.fn>;
    debug: ReturnType<typeof vi.fn>;
  };
  runBootstrap: (name: string) => Promise<void>;
}> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-skills-ext-"));
  let factory: ExtensionFactory | null = null;
  let useOptions: UseFactoryOptions | undefined;
  const bootstrapHooks = new Map<string, () => void | Promise<void>>();

  const log = { info: vi.fn(), warn: vi.fn(), debug: vi.fn() };

  const app = {
    extensionConfig: config,
    log,
    workspace: { resolve: (...segments: string[]) => join(workspaceDir, ...segments) },
    bootstrap: vi.fn((name: string, hook: () => void | Promise<void>) => {
      bootstrapHooks.set(name, hook);
    }),
    agent: {
      use: (registered: ExtensionFactory, options?: UseFactoryOptions) => {
        factory = registered;
        useOptions = options;
      },
      side: {},
    },
  } as unknown as AppContext<{ enabled: boolean }>;

  skills.setup(app);

  const on = vi.fn();
  const registerTool = vi.fn();
  await (factory as ExtensionFactory | null)?.({
    on,
    registerTool,
    registerCommand: vi.fn(),
    sendUserMessage: vi.fn(),
  } as unknown as ExtensionAPI);

  return {
    workspaceDir,
    workspaceSkillsDir: join(workspaceDir, "skills"),
    on,
    registerTool,
    useOptions,
    log,
    runBootstrap: async (name: string) => {
      await bootstrapHooks.get(name)?.();
    },
  };
};

describe("skills extension", () => {
  it("contributes the workspace and built-in skills directories as skill sources", async () => {
    const { workspaceSkillsDir, on } = await setup();

    const handler = on.mock.calls.find(([event]) => event === "resources_discover")?.[1];

    expect(handler).toBeDefined();
    expect(handler()).toEqual({ skillPaths: [workspaceSkillsDir, repoSkillsDir] });
  });

  it("resolves the built-in directory to the repo's shipped authoring skills", () => {
    expect(existsSync(join(repoSkillsDir, "skill-authoring", "SKILL.md"))).toBe(true);
    expect(existsSync(join(repoSkillsDir, "workflow-authoring", "SKILL.md"))).toBe(true);
  });

  it("registers delegate_to_agent with the built-in general-purpose agent even with no skill agents", async () => {
    const { registerTool } = await setup();

    const delegate = registerTool.mock.calls
      .map(([definition]) => definition as { name: string; description: string })
      .find((definition) => definition.name === "delegate_to_agent");

    expect(delegate).toBeDefined();
    expect(delegate?.description).toContain("general-purpose:");
  });

  it("opts its factory into background task runs so background tasks get skills and delegation", async () => {
    const { useOptions } = await setup();

    expect(useOptions?.sessionScopes).toContain("background");
  });

  it("creates the workspace skills directory on bootstrap", async () => {
    const { workspaceSkillsDir, runBootstrap } = await setup();

    expect(existsSync(workspaceSkillsDir)).toBe(false);

    await runBootstrap("ensure-skills-dir");

    expect(existsSync(workspaceSkillsDir)).toBe(true);

    await rm(workspaceSkillsDir, { recursive: true, force: true });
  });

  it("does nothing but log when disabled by configuration", async () => {
    const { on, registerTool, useOptions, log } = await setup({ enabled: false });

    expect(log.info).toHaveBeenCalledWith("skills disabled by configuration");
    expect(on).not.toHaveBeenCalled();
    expect(registerTool).not.toHaveBeenCalled();
    expect(useOptions).toBeUndefined();
  });
});

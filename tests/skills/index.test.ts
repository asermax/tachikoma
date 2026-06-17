import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import type {
  AgentExtensionFactory,
  AppContext,
  SessionScope,
  UseFactoryOptions,
} from "../../src/extensions/api.ts";
import skills from "../../src/extensions/skills/index.ts";

const repoSkillsDir = resolve(import.meta.dirname, "../../src/extensions/skills/builtin-skills");

const setup = async (
  config: { enabled: boolean; proactiveLoading?: boolean } = {
    enabled: true,
    proactiveLoading: true,
  },
  scope: SessionScope = "main",
): Promise<{
  workspaceDir: string;
  workspaceSkillsDir: string;
  on: ReturnType<typeof vi.fn>;
  registerTool: ReturnType<typeof vi.fn>;
  useOptions: UseFactoryOptions | undefined;
  status: ReturnType<typeof vi.fn>;
  classify: ReturnType<typeof vi.fn>;
  log: {
    info: ReturnType<typeof vi.fn>;
    warn: ReturnType<typeof vi.fn>;
    debug: ReturnType<typeof vi.fn>;
  };
  runBootstrap: (name: string) => Promise<void>;
  fireSkillSuggestion: (
    skills: { name: string; description: string; filePath: string }[],
  ) => Promise<void>;
}> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-skills-ext-"));
  let factory: AgentExtensionFactory | null = null;
  let useOptions: UseFactoryOptions | undefined;
  const bootstrapHooks = new Map<string, () => void | Promise<void>>();

  const log = { info: vi.fn(), warn: vi.fn(), debug: vi.fn() };
  const status = vi.fn();
  const classify = vi.fn().mockResolvedValue({ skills: [] });

  const app = {
    extensionConfig: config,
    log,
    status,
    workspace: { resolve: (...segments: string[]) => join(workspaceDir, ...segments) },
    bootstrap: vi.fn((name: string, hook: () => void | Promise<void>) => {
      bootstrapHooks.set(name, hook);
    }),
    agent: {
      use: (registered: AgentExtensionFactory, options?: UseFactoryOptions) => {
        factory = registered;
        useOptions = options;
      },
      side: { classify },
      isForking: () => false,
    },
  } as unknown as AppContext<{ enabled: boolean; proactiveLoading?: boolean }>;

  skills.setup(app);

  const on = vi.fn();
  const registerTool = vi.fn();
  await (factory as AgentExtensionFactory | null)?.(
    {
      on,
      registerTool,
      registerCommand: vi.fn(),
      sendUserMessage: vi.fn(),
    } as unknown as ExtensionAPI,
    { scope },
  );

  const fireSkillSuggestion = async (
    skillCatalog: { name: string; description: string; filePath: string }[],
  ): Promise<void> => {
    const handler = on.mock.calls.find(([event]) => event === "before_agent_start")?.[1] as
      | ((
          event: { prompt: string; systemPromptOptions: { skills: unknown[] } },
          ctx: { sessionManager: { getEntries: () => unknown[]; getLeafId: () => string | null } },
        ) => Promise<unknown>)
      | undefined;
    await handler?.(
      { prompt: "do a thing", systemPromptOptions: { skills: skillCatalog } },
      { sessionManager: { getEntries: () => [], getLeafId: () => null } },
    );
  };

  return {
    workspaceDir,
    workspaceSkillsDir: join(workspaceDir, "skills"),
    on,
    registerTool,
    useOptions,
    status,
    classify,
    log,
    runBootstrap: async (name: string) => {
      await bootstrapHooks.get(name)?.();
    },
    fireSkillSuggestion,
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

  it("registers the proactive skill-suggestion handler when proactiveLoading is on", async () => {
    const { on } = await setup({ enabled: true, proactiveLoading: true });

    const handlers = on.mock.calls.filter(([event]) => event === "before_agent_start");

    expect(handlers).toHaveLength(1);
  });

  it("surfaces the skill-suggestion status for a main session", async () => {
    const { status, fireSkillSuggestion } = await setup(
      { enabled: true, proactiveLoading: true },
      "main",
    );

    await fireSkillSuggestion([
      { name: "pdf-tools", description: "Work with PDFs", filePath: "/skills/pdf-tools/SKILL.md" },
    ]);

    expect(status).toHaveBeenCalledWith("Checking for relevant skills…");
  });

  it("keeps the classifier but suppresses the skill-suggestion status for a background session (no stray)", async () => {
    const { status, classify, fireSkillSuggestion } = await setup(
      { enabled: true, proactiveLoading: true },
      "background",
    );

    await fireSkillSuggestion([
      { name: "pdf-tools", description: "Work with PDFs", filePath: "/skills/pdf-tools/SKILL.md" },
    ]);

    // A background task session has no user-facing surface, so the status must not surface (no stray
    // lead-in) — but the proactive classifier still runs so background tasks keep skill injection.
    expect(classify).toHaveBeenCalledTimes(1);
    expect(status).not.toHaveBeenCalled();
  });

  it("registers no skill-suggestion handler when proactiveLoading is off", async () => {
    const { on } = await setup({ enabled: true, proactiveLoading: false });

    const handlers = on.mock.calls.filter(([event]) => event === "before_agent_start");

    expect(handlers).toHaveLength(0);
  });

  it("defaults proactiveLoading to true in the config schema", () => {
    const schema = skills.configSchema as {
      properties: { proactiveLoading: { default: boolean } };
    };

    expect(schema.properties.proactiveLoading.default).toBe(true);
  });

  it("does nothing but log when disabled by configuration", async () => {
    const { on, registerTool, useOptions, log } = await setup({ enabled: false });

    expect(log.debug).toHaveBeenCalledWith("skills disabled by configuration");
    expect(on).not.toHaveBeenCalled();
    expect(registerTool).not.toHaveBeenCalled();
    expect(useOptions).toBeUndefined();
  });
});

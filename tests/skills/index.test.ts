import { existsSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import type { ExtensionAPI, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import type { AppContext } from "../../src/extensions/api.ts";
import skills from "../../src/extensions/skills/index.ts";

const repoSkillsDir = resolve(import.meta.dirname, "../../skills");

const setup = async (): Promise<{ workspaceSkillsDir: string; on: ReturnType<typeof vi.fn> }> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-skills-ext-"));
  let factory: ExtensionFactory | null = null;

  const app = {
    extensionConfig: { enabled: true },
    log: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() },
    workspace: { resolve: (...segments: string[]) => join(workspaceDir, ...segments) },
    bootstrap: vi.fn(),
    agent: {
      use: (registered: ExtensionFactory) => {
        factory = registered;
      },
      side: {},
    },
  } as unknown as AppContext<{ enabled: boolean }>;

  skills.setup(app);

  const on = vi.fn();
  await (factory as ExtensionFactory | null)?.({
    on,
    registerTool: vi.fn(),
    registerCommand: vi.fn(),
    sendUserMessage: vi.fn(),
  } as unknown as ExtensionAPI);

  return { workspaceSkillsDir: join(workspaceDir, "skills"), on };
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
});

import { mkdir, mkdtemp, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import { discoverSkillAgents } from "../../src/extensions/skills/agents.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), warn: vi.fn() } as unknown as Logger;

let skillsRoot: string;

const writeAgent = async (skill: string, file: string, content: string): Promise<void> => {
  const agentsDir = join(skillsRoot, skill, "agents");

  await mkdir(agentsDir, { recursive: true });
  await writeFile(join(agentsDir, file), content);
};

beforeEach(async () => {
  skillsRoot = await mkdtemp(join(tmpdir(), "tachi-skills-"));
});

describe("discoverSkillAgents", () => {
  it("discovers agents namespaced by skill with frontmatter metadata", async () => {
    await writeAgent(
      "research",
      "scout.md",
      "---\ndescription: Finds sources\ntools:\n  - read\n  - grep\n---\n\nYou are a scout.",
    );

    const agents = discoverSkillAgents(skillsRoot, fakeLog);

    expect(agents).toEqual([
      {
        name: "research/scout",
        description: "Finds sources",
        tools: ["read", "grep"],
        model: null,
        systemPrompt: "You are a scout.",
        skill: "research",
      },
    ]);
  });

  it("parses an explicit model reference from frontmatter", async () => {
    await writeAgent(
      "research",
      "analyst.md",
      "---\ndescription: Analyzes\nmodel: anthropic/claude-opus-4-5:high\n---\n\nBody.",
    );

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.model).toBe(
      "anthropic/claude-opus-4-5:high",
    );
  });

  it("defaults model to null when not declared", async () => {
    await writeAgent("research", "scout.md", "---\ndescription: Finds sources\n---\n\nBody.");

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.model).toBeNull();
  });

  it("warns and falls back to null on an invalid model type, keeping the agent", async () => {
    await writeAgent(
      "research",
      "scout.md",
      "---\ndescription: Finds sources\nmodel:\n  - not-a-string\n---\n\nBody.",
    );

    const agents = discoverSkillAgents(skillsRoot, fakeLog);

    expect(agents[0]?.name).toBe("research/scout");
    expect(agents[0]?.model).toBeNull();
  });

  it("prefers an explicit frontmatter name and parses comma-separated tools", async () => {
    await writeAgent(
      "research",
      "scout.md",
      "---\nname: pathfinder\ndescription: Finds paths\ntools: read, ls\n---\n\nBody.",
    );

    const agents = discoverSkillAgents(skillsRoot, fakeLog);

    expect(agents[0]?.name).toBe("research/pathfinder");
    expect(agents[0]?.tools).toEqual(["read", "ls"]);
  });

  it("defaults tools to null when not declared", async () => {
    await writeAgent("research", "scout.md", "---\ndescription: Finds sources\n---\n\nBody.");

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.tools).toBeNull();
  });

  it("warns and falls back to the default tool set on malformed tools, keeping the agent", async () => {
    const warn = vi.mocked(fakeLog.warn).mockClear();

    await writeAgent(
      "research",
      "scout.md",
      "---\ndescription: Finds sources\ntools:\n  nested: true\n---\n\nBody.",
    );

    const agents = discoverSkillAgents(skillsRoot, fakeLog);

    expect(agents[0]?.name).toBe("research/scout");
    expect(agents[0]?.tools).toBeNull();
    expect(warn).toHaveBeenCalledWith(
      { skill: "research", agent: "scout.md" },
      "skill agent has invalid tools format — using default tool set",
    );
  });

  it("skips agents without a description", async () => {
    await writeAgent("research", "broken.md", "---\ntools: read\n---\n\nNo description.");
    await writeAgent("research", "valid.md", "---\ndescription: Works\n---\n\nBody.");

    const agents = discoverSkillAgents(skillsRoot, fakeLog);

    expect(agents.map((agent) => agent.name)).toEqual(["research/valid"]);
  });

  it("returns an empty list for skills without agents and missing roots", async () => {
    await mkdir(join(skillsRoot, "empty-skill"), { recursive: true });

    expect(discoverSkillAgents(skillsRoot, fakeLog)).toEqual([]);
    expect(discoverSkillAgents(join(skillsRoot, "nope"), fakeLog)).toEqual([]);
  });

  it("treats an empty string model as null", async () => {
    await writeAgent(
      "research",
      "scout.md",
      '---\ndescription: Finds sources\nmodel: ""\n---\n\nBody.',
    );

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.model).toBeNull();
  });

  it("falls back to the stem when the frontmatter name is an empty string", async () => {
    await writeAgent(
      "research",
      "scout.md",
      '---\ndescription: Finds sources\nname: ""\n---\n\nBody.',
    );

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.name).toBe("research/scout");
  });

  it("treats an empty comma-separated tools string as null", async () => {
    await writeAgent(
      "research",
      "scout.md",
      '---\ndescription: Finds sources\ntools: " , , "\n---\n\nBody.',
    );

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.tools).toBeNull();
  });

  it("treats an empty tools list as null", async () => {
    await writeAgent(
      "research",
      "scout.md",
      "---\ndescription: Finds sources\ntools: []\n---\n\nBody.",
    );

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.tools).toBeNull();
  });

  it("warns on a tools array containing non-string entries and falls back to null", async () => {
    await writeAgent(
      "research",
      "scout.md",
      "---\ndescription: Finds sources\ntools:\n  - read\n  - 5\n---\n\nBody.",
    );

    expect(discoverSkillAgents(skillsRoot, fakeLog)[0]?.tools).toBeNull();
  });

  it("discovers agents reachable through a symlinked markdown file", async () => {
    await writeAgent("research", "real.md", "---\ndescription: Real agent\n---\n\nBody.");

    const agentsDir = join(skillsRoot, "research", "agents");
    await symlink(join(agentsDir, "real.md"), join(agentsDir, "linked.md"));

    expect(
      discoverSkillAgents(skillsRoot, fakeLog)
        .map((agent) => agent.name)
        .sort(),
    ).toEqual(["research/linked", "research/real"]);
  });

  it("logs and skips an agent whose file cannot be read", async () => {
    const warn = vi.mocked(fakeLog.warn).mockClear();

    const agentsDir = join(skillsRoot, "research", "agents");
    await mkdir(agentsDir, { recursive: true });
    await symlink(join(agentsDir, "missing-target.md"), join(agentsDir, "dangling.md"));

    const agents = discoverSkillAgents(skillsRoot, fakeLog);

    expect(agents).toEqual([]);
    expect(warn).toHaveBeenCalledWith(
      expect.objectContaining({ skill: "research", agent: "dangling.md" }),
      "failed to load skill agent — skipped",
    );
  });
});

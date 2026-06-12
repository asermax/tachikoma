import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
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
        systemPrompt: "You are a scout.",
        skill: "research",
      },
    ]);
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
});

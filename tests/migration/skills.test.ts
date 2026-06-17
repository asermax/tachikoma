import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { Logger } from "../../src/log.ts";
import { adaptSkillsFrontmatter, stripLegacyFrontmatter } from "../../src/migration/skills.ts";
import { Workspace } from "../../src/workspace.ts";

const fakeLog = Object.assign(
  { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  { child: () => fakeLog },
) as unknown as Logger;

const LEGACY_SKILL = `---
description: Research assistant
version: "1.2.0"
depends_on:
  - planning
  - notes
---

# Research

Body content.
`;

const CLEAN_SKILL = `---
name: notes
description: Note keeping
---

# Notes
`;

const makeSkill = async (workspace: Workspace, name: string, content: string): Promise<string> => {
  const dir = workspace.resolve("skills", name);
  await mkdir(dir, { recursive: true });

  const file = join(dir, "SKILL.md");
  await writeFile(file, content, "utf8");
  return file;
};

describe("stripLegacyFrontmatter", () => {
  it("removes version and block-list depends_on, keeping everything else", () => {
    const scan = stripLegacyFrontmatter(LEGACY_SKILL);

    expect(scan).not.toBeNull();
    expect(scan?.keys.sort()).toEqual(["depends_on", "version"]);
    expect(scan?.content).toContain("description: Research assistant");
    expect(scan?.content).not.toContain("version");
    expect(scan?.content).not.toContain("depends_on");
    expect(scan?.content).not.toContain("- planning");
    expect(scan?.content).toContain("# Research");
  });

  it("removes inline-list depends_on", () => {
    const scan = stripLegacyFrontmatter("---\ndescription: x\ndepends_on: [a, b]\n---\nbody\n");

    expect(scan?.keys).toEqual(["depends_on"]);
    expect(scan?.content).toBe("---\ndescription: x\n---\nbody\n");
  });

  it("returns null for skills without legacy-only keys", () => {
    expect(stripLegacyFrontmatter(CLEAN_SKILL)).toBeNull();
  });

  it("returns null for files without frontmatter", () => {
    expect(stripLegacyFrontmatter("# Just markdown\n")).toBeNull();
  });
});

describe("adaptSkillsFrontmatter", () => {
  const makeWorkspace = async (): Promise<Workspace> => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-migration-skills-"));
    return new Workspace(dir);
  };

  it("strips keys from affected skills when confirmed", async () => {
    const workspace = await makeWorkspace();
    const legacyFile = await makeSkill(workspace, "research", LEGACY_SKILL);
    const cleanFile = await makeSkill(workspace, "notes", CLEAN_SKILL);
    const ask = vi.fn(async () => true);

    await adaptSkillsFrontmatter(workspace, fakeLog, ask);

    expect(ask).toHaveBeenCalledOnce();
    expect(ask.mock.calls[0]?.[0]).toContain("1 skill(s)");
    await expect(readFile(legacyFile, "utf8")).resolves.not.toContain("depends_on");
    await expect(readFile(cleanFile, "utf8")).resolves.toBe(CLEAN_SKILL);
  });

  it("keeps files untouched and warns when declined", async () => {
    const workspace = await makeWorkspace();
    const legacyFile = await makeSkill(workspace, "research", LEGACY_SKILL);

    await adaptSkillsFrontmatter(workspace, fakeLog, async () => false);

    await expect(readFile(legacyFile, "utf8")).resolves.toBe(LEGACY_SKILL);
    expect(fakeLog.warn).toHaveBeenCalled();
  });

  it("does not prompt when no skill carries legacy-only keys", async () => {
    const workspace = await makeWorkspace();
    await makeSkill(workspace, "notes", CLEAN_SKILL);
    const ask = vi.fn(async () => true);

    await adaptSkillsFrontmatter(workspace, fakeLog, ask);

    expect(ask).not.toHaveBeenCalled();
  });

  it("is a no-op without a skills directory", async () => {
    const workspace = await makeWorkspace();
    const ask = vi.fn(async () => true);

    await expect(adaptSkillsFrontmatter(workspace, fakeLog, ask)).resolves.toBeUndefined();

    expect(ask).not.toHaveBeenCalled();
  });
});

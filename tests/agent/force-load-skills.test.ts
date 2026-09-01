import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { ExtensionAPI, Skill } from "@earendil-works/pi-coding-agent";
import { DefaultResourceLoader } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forceLoadSkillsFactory } from "../../src/agent/force-load-skills.ts";
import { AUTHORING_GUIDE_SKILLS } from "../../src/extensions/skill-evolution/prompts.ts";
import type { Logger } from "../../src/log.ts";
import { builtinSkillsDir } from "../../src/util/builtin-skills.ts";
import { fakeLogger, makeTempDir } from "../git/helpers.ts";

const log = fakeLogger() as unknown as Logger;

beforeEach(() => {
  vi.clearAllMocks();
});

type Handler = (event: {
  prompt: string;
  systemPromptOptions: { skills?: Skill[] };
}) => Promise<{ message: { customType: string; content: string; display: false } } | undefined>;

/** Register the factory against a fake `pi` and hand back the captured handler (suggest.ts idiom). */
const register = (names: readonly string[], readSkill?: (filePath: string) => string): Handler => {
  let handler: Handler | undefined;
  const pi = {
    on: (event: string, registered: unknown) => {
      if (event === "before_agent_start") handler = registered as Handler;
    },
  } as unknown as ExtensionAPI;

  forceLoadSkillsFactory({ names, log, ...(readSkill != null ? { readSkill } : {}) })(pi);

  if (handler == null) throw new Error("factory did not register a before_agent_start handler");
  return handler;
};

const makeSkill = (name: string, filePath: string): Skill =>
  ({
    name,
    description: `${name} description`,
    filePath,
    baseDir: filePath.slice(0, filePath.lastIndexOf("/")),
    sourceInfo: undefined,
    disableModelInvocation: false,
  }) as Skill;

const fire = (handler: Handler, skills: Skill[]) =>
  handler({ prompt: "task", systemPromptOptions: { skills } });

describe("forceLoadSkillsFactory", () => {
  it("injects every resolved skill's stripped body under the grounding preface", async () => {
    // Real frontmatter — exercises the real stripFrontmatter the factory runs.
    const readSkill = (filePath: string) =>
      filePath.includes("workflow")
        ? "---\nname: workflow-authoring\n---\n\n## Workflow guide body.\n"
        : "---\nname: skill-authoring\n---\n\n## Skill guide body.\n";
    const handler = register(["skill-authoring", "workflow-authoring"], readSkill);

    const result = await fire(handler, [
      makeSkill("skill-authoring", "/guides/skill-authoring/SKILL.md"),
      makeSkill("workflow-authoring", "/guides/workflow-authoring/SKILL.md"),
    ]);

    expect(result).toBeDefined();
    expect(result?.message.customType).toBe("skill-content");
    expect(result?.message.display).toBe(false);
    expect(result?.message.content).toContain("force-loaded for this run");
    expect(result?.message.content).toContain('<injected-skill name="skill-authoring">');
    expect(result?.message.content).toContain("## Skill guide body.");
    expect(result?.message.content).toContain('<injected-skill name="workflow-authoring">');
    expect(result?.message.content).toContain("## Workflow guide body.");
    // Frontmatter is catalog metadata, never injected.
    expect(result?.message.content).not.toContain("name: skill-authoring");
  });

  it("skips a name absent from the catalog with a warning and injects the rest", async () => {
    const readSkill = () => "---\nname: x\n---\n\n## Guide body.\n";
    const handler = register(["skill-authoring", "gone-guide"], readSkill);

    const result = await fire(handler, [
      makeSkill("skill-authoring", "/guides/skill-authoring/SKILL.md"),
    ]);

    expect(result?.message.content).toContain('<injected-skill name="skill-authoring">');
    expect(result?.message.content).not.toContain("gone-guide");
    expect(log.warn).toHaveBeenCalledWith(
      { skill: "gone-guide" },
      "force-loaded skill not in the session catalog — skipping",
    );
  });

  it("skips an unreadable skill file with a warning", async () => {
    const readSkill = () => {
      throw new Error("EACCES");
    };
    const handler = register(["skill-authoring"], readSkill);

    const result = await fire(handler, [makeSkill("skill-authoring", "/guides/a/SKILL.md")]);

    expect(result).toBeUndefined();
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ skill: "skill-authoring" }),
      "failed to read force-loaded skill content — skipping injection",
    );
  });

  it("skips an empty body at debug without warning", async () => {
    const readSkill = () => "---\nname: skill-authoring\n---\n\n";
    const handler = register(["skill-authoring"], readSkill);

    const result = await fire(handler, [makeSkill("skill-authoring", "/guides/a/SKILL.md")]);

    expect(result).toBeUndefined();
    expect(log.warn).not.toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith(
      { skill: "skill-authoring" },
      "force-loaded skill content is empty — skipping injection",
    );
  });

  it("returns undefined when nothing is loadable (the run proceeds ungrounded)", async () => {
    const handler = register(["a", "b"]);

    expect(await fire(handler, [])).toBeUndefined();
    expect(log.warn).toHaveBeenCalledTimes(2);
  });

  it("never re-injects a skill that already landed; a skipped name retries next turn", async () => {
    let body = "first read wins";
    const readSkill = () => `---\nname: x\n---\n\n${body}\n`;
    const handler = register(["skill-authoring", "gone-guide"], readSkill);

    const catalog = [makeSkill("skill-authoring", "/guides/skill-authoring/SKILL.md")];

    const first = await fire(handler, catalog);
    expect(first?.message.content).toContain("first read wins");

    body = "second read would differ";
    const second = await fire(handler, catalog);
    // The injected name is latched (no re-injection — the changed body never lands); the absent
    // name retried and warned again on this turn.
    expect(second).toBeUndefined();
    expect(log.warn).toHaveBeenCalledWith(
      { skill: "gone-guide" },
      "force-loaded skill not in the session catalog — skipping",
    );
    expect(log.warn).toHaveBeenCalledTimes(2);
  });
});

// The loader composition the design leans on: explicitly added skill paths are merged even under
// noSkills, and load with includeDefaults: false — so an isolated run's catalog is exactly the
// passed directory's skills. Real SDK (this file never mocks the package), loader-level real
// integration in temp dirs (DES-003 §4) — no pi session is created (§5). Decoys in the default
// discovery locations prove the isolation half rather than passing vacuously.
describe("loader composition (noSkills + additionalSkillPaths)", () => {
  let base: string;

  beforeEach(async () => {
    base = await makeTempDir();
  });

  afterEach(async () => {
    await rm(base, { recursive: true, force: true });
  });

  it("discovers exactly the passed directory's skills — decoys stay out", async () => {
    const cwd = join(base, "cwd");
    const agentDir = join(base, "agent");
    await mkdir(join(agentDir, "skills", "agent-decoy"), { recursive: true });
    await writeFile(
      join(agentDir, "skills", "agent-decoy", "SKILL.md"),
      "---\nname: agent-decoy\ndescription: decoy under agentDir/skills\n---\n\nbody\n",
    );
    await mkdir(join(cwd, ".pi", "skills", "cwd-decoy"), { recursive: true });
    await writeFile(
      join(cwd, ".pi", "skills", "cwd-decoy", "SKILL.md"),
      "---\nname: cwd-decoy\ndescription: decoy under cwd/.pi/skills\n---\n\nbody\n",
    );

    const loader = new DefaultResourceLoader({
      cwd,
      agentDir,
      noSkills: true,
      additionalSkillPaths: [builtinSkillsDir],
    });
    await loader.reload();

    const names = loader
      .getSkills()
      .skills.map((skill) => skill.name)
      .sort();
    expect(names).toEqual(["skill-authoring", "workflow-authoring"]);
  });
});

// End to end over the real bundled guides: real loader discovery, the default `readFileSync`
// reader, and the real `stripFrontmatter` — the guides must be injectable, not just discoverable.
describe("real guides (end to end)", () => {
  it("injects both bundled guides' real bodies from the real loader catalog", async () => {
    const loader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: join(process.cwd(), ".tachikoma"),
      noSkills: true,
      additionalSkillPaths: [builtinSkillsDir],
    });
    await loader.reload();

    // register() without a reader uses the default real reader — the production path.
    const handler = register([...AUTHORING_GUIDE_SKILLS]);
    const result = await fire(handler, loader.getSkills().skills);

    expect(result).toBeDefined();
    const sections = [
      ...(result?.message.content.matchAll(
        /<injected-skill name="([^"]+)">\n([\s\S]*?)\n<\/injected-skill>/g,
      ) ?? []),
    ];
    expect(sections.map(([, name]) => name)).toEqual([...AUTHORING_GUIDE_SKILLS]);
    for (const [, , body] of sections) {
      expect(body.trim().length).toBeGreaterThan(100);
    }
  });
});

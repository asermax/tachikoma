import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import {
  findWorkflow,
  loadAllWorkflows,
  loadSkillWorkflows,
} from "../../src/extensions/workflows/loader.ts";
import { createFakeLog, writeWorkflowFixture } from "./helpers.ts";

const log = createFakeLog();

let skillsRoot: string;

beforeEach(async () => {
  skillsRoot = await mkdtemp(join(tmpdir(), "tachi-workflows-loader-"));
});

describe("findWorkflow", () => {
  it("loads steps in directory order with parsed frontmatter", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "02-review", frontmatter: "title: Review\nrequired: false" },
      { id: "01-plan", frontmatter: "title: Plan\naudience: internal" },
    ]);

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition).not.toBeNull();
    expect(definition?.steps.map((step) => step.id)).toEqual(["01-plan", "02-review"]);
    expect(definition?.steps[0]).toMatchObject({
      title: "Plan",
      required: true,
      properties: { audience: "internal" },
    });
    expect(definition?.steps[1]).toMatchObject({ title: "Review", required: false });
  });

  it("treats the deprecated skippable field as required: false", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-legacy", frontmatter: "title: Legacy\nskippable: true" },
    ]);

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps[0]?.required).toBe(false);
  });

  it("skips step directories without instructions.md or a valid title", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-plan", frontmatter: "title: Plan" },
      { id: "03-untitled", frontmatter: "required: false" },
    ]);
    await mkdir(join(skillsRoot, "writing", "workflows", "draft", "02-empty"), {
      recursive: true,
    });

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps.map((step) => step.id)).toEqual(["01-plan"]);
  });

  it("detects references and scripts subdirectories", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-plan", frontmatter: "title: Plan" },
    ]);
    const stepDir = join(skillsRoot, "writing", "workflows", "draft", "01-plan");
    await mkdir(join(stepDir, "references"));

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps[0]?.referencesPath).toBe(join(stepDir, "references"));
    expect(definition?.steps[0]?.scriptsPath).toBeNull();
  });

  it("returns null when the workflow directory does not exist", () => {
    expect(findWorkflow(skillsRoot, "writing", "missing", log)).toBeNull();
  });

  it("skips a step whose instructions.md frontmatter cannot be parsed", async () => {
    const stepDir = join(skillsRoot, "writing", "workflows", "draft", "01-broken");
    await mkdir(stepDir, { recursive: true });
    await writeFile(join(stepDir, "instructions.md"), "---\ntitle: [unterminated\n---\nbody\n");

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps).toEqual([]);
  });

  it("skips a step whose required field is not a boolean", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-bad", frontmatter: "title: Bad\nrequired: maybe" },
      { id: "02-good", frontmatter: "title: Good" },
    ]);

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps.map((step) => step.id)).toEqual(["02-good"]);
  });

  it("parses valid condition, composes, and loop fields", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      {
        id: "01-plan",
        frontmatter: "title: Plan\ncondition: when ready\ncomposes: other-skill/sub",
      },
      { id: "02-each", frontmatter: "title: Each\nloop: writing/iter" },
    ]);

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps[0]).toMatchObject({
      condition: "when ready",
      composes: "other-skill/sub",
    });
    expect(definition?.steps[1]?.loop).toBe("writing/iter");
  });

  it("treats a mistyped composition field as unset", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-plan", frontmatter: "title: Plan\ncomposes: 42" },
    ]);

    const definition = findWorkflow(skillsRoot, "writing", "draft", log);

    expect(definition?.steps[0]?.composes).toBeNull();
  });
});

describe("loadAllWorkflows", () => {
  it("indexes every workflow across every skill by skill/workflow key", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-plan", frontmatter: "title: Plan" },
    ]);
    await writeWorkflowFixture(skillsRoot, "research", "explore", [
      { id: "01-search", frontmatter: "title: Search" },
    ]);

    const all = loadAllWorkflows(skillsRoot, log);

    expect([...all.keys()].sort()).toEqual(["research/explore", "writing/draft"]);
    expect(all.get("writing/draft")?.steps[0]?.title).toBe("Plan");
  });

  it("returns an empty map when the skills root has no skills", () => {
    expect(loadAllWorkflows(join(skillsRoot, "nope"), log).size).toBe(0);
  });
});

describe("loadSkillWorkflows", () => {
  it("discovers every workflow under a skill's workflows directory", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "draft", [
      { id: "01-plan", frontmatter: "title: Plan" },
    ]);
    await writeWorkflowFixture(skillsRoot, "writing", "publish", [
      { id: "01-ship", frontmatter: "title: Ship" },
    ]);

    const workflows = loadSkillWorkflows(join(skillsRoot, "writing"), "writing", log);

    expect(workflows.map((workflow) => workflow.workflowName).sort()).toEqual(["draft", "publish"]);
  });

  it("returns an empty list when the skill has no workflows directory", () => {
    expect(loadSkillWorkflows(join(skillsRoot, "missing"), "missing", log)).toEqual([]);
  });
});

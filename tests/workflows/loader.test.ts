import { mkdir, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { findWorkflow, loadSkillWorkflows } from "../../src/extensions/workflows/loader.ts";
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

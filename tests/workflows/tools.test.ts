import { existsSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { findWorkflow } from "../../src/extensions/workflows/loader.ts";
import { WorkflowStateRepository } from "../../src/extensions/workflows/repository.ts";
import {
  handleEndWorkflow,
  handleQueryWorkflow,
  handleStartWorkflow,
  handleUpdateWorkflowState,
  type WorkflowToolDeps,
} from "../../src/extensions/workflows/tools.ts";
import { createFakeLog, createTestDatabase, writeWorkflowFixture } from "./helpers.ts";

const log = createFakeLog();

let deps: WorkflowToolDeps;
let repository: WorkflowStateRepository;

beforeEach(async () => {
  const skillsRoot = await mkdtemp(join(tmpdir(), "tachi-workflows-skills-"));

  await writeWorkflowFixture(skillsRoot, "writing", "draft", [
    { id: "01-plan", frontmatter: "title: Plan", body: "Sketch the outline." },
    { id: "02-research", frontmatter: "title: Research\nrequired: false" },
    { id: "03-write", frontmatter: "title: Write" },
  ]);

  repository = new WorkflowStateRepository(await createTestDatabase());

  deps = {
    repository,
    findWorkflow: (skill, workflow) => findWorkflow(skillsRoot, skill, workflow, log),
    scratchpadDir: await mkdtemp(join(tmpdir(), "tachi-workflows-pads-")),
    log,
  };
});

const startDraft = () => {
  handleStartWorkflow(deps, "writing", "draft");
  const state = repository.getActive("writing", "draft");

  if (state == null) throw new Error("workflow was not persisted");

  return state;
};

describe("handleStartWorkflow", () => {
  it("creates a tracked instance with a scratchpad and returns guidance", () => {
    const guidance = handleStartWorkflow(deps, "writing", "draft");
    const state = repository.getActive("writing", "draft");

    expect(state).not.toBeNull();
    expect(state?.stepStates).toEqual({
      "01-plan": "pending",
      "02-research": "pending",
      "03-write": "pending",
    });
    expect(existsSync(state?.scratchpadPath ?? "")).toBe(true);

    expect(guidance).toContain("Workflow started: **draft**");
    expect(guidance).toContain("**Plan** (`01-plan`)");
    expect(guidance).toContain("**Research** (`02-research`) (skippable)");
    expect(guidance).toContain(state?.id);
  });

  it("rejects unknown workflows", () => {
    expect(() => handleStartWorkflow(deps, "writing", "missing")).toThrow(/not found in skill/);
  });

  it("rejects a second start while an instance is active", () => {
    const state = startDraft();

    expect(() => handleStartWorkflow(deps, "writing", "draft")).toThrow(
      new RegExp(`already active.*${state.id}`, "s"),
    );
  });
});

describe("handleUpdateWorkflowState", () => {
  it("starts a step and returns its instructions", () => {
    const state = startDraft();

    const response = handleUpdateWorkflowState(deps, state.id, "01-plan", "start");

    expect(response).toContain("Step **Plan** (`01-plan`) started.");
    expect(response).toContain("Sketch the outline.");
    expect(repository.get(state.id)?.currentStep).toBe("01-plan");
    expect(repository.get(state.id)?.stepStates["01-plan"]).toBe("started");
  });

  it("auto-starts the next pending step on complete", () => {
    const state = startDraft();
    handleUpdateWorkflowState(deps, state.id, "01-plan", "start");

    const response = handleUpdateWorkflowState(deps, state.id, "01-plan", "complete");

    expect(response).toContain("Step `01-plan` completed.");
    expect(response).toContain("Next step **Research** (`02-research`) started.");
    expect(repository.get(state.id)?.currentStep).toBe("02-research");
  });

  it("skips a pending optional step and auto-starts the next pending step", () => {
    const state = startDraft();

    const response = handleUpdateWorkflowState(deps, state.id, "02-research", "skip");

    expect(repository.get(state.id)?.stepStates["02-research"]).toBe("skipped");
    expect(response).toContain("Next step **Plan** (`01-plan`) started.");
  });

  it("auto-finalizes when the last step completes", () => {
    const state = startDraft();
    handleUpdateWorkflowState(deps, state.id, "02-research", "skip");
    handleUpdateWorkflowState(deps, state.id, "01-plan", "complete");

    const response = handleUpdateWorkflowState(deps, state.id, "03-write", "complete");

    expect(response).toContain("Workflow complete and finalized!");
    expect(response).toContain("2 completed, 1 skipped");
    expect(repository.get(state.id)).toBeNull();
    expect(existsSync(state.scratchpadPath)).toBe(false);
  });

  it("rejects invalid transitions", () => {
    const state = startDraft();

    expect(() => handleUpdateWorkflowState(deps, state.id, "01-plan", "complete")).toThrow(
      /Must start a step before completing it/,
    );
    expect(() => handleUpdateWorkflowState(deps, state.id, "01-plan", "skip")).toThrow(
      /required and cannot be skipped/,
    );
    expect(() => handleUpdateWorkflowState(deps, state.id, "99-nope", "start")).toThrow(
      /Invalid step '99-nope'/,
    );
    expect(() => handleUpdateWorkflowState(deps, "missing-id", "01-plan", "start")).toThrow(
      /not found or no longer active/,
    );
  });
});

describe("handleQueryWorkflow", () => {
  it("renders the full state view for a workflow id", () => {
    const state = startDraft();
    handleUpdateWorkflowState(deps, state.id, "01-plan", "start");

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain(`- **ID**: ${state.id}`);
    expect(view).toContain("- **Current Step**: 01-plan");
    expect(view).toContain("- **Plan** (`01-plan`): started");
    expect(view).toContain("- **Write** (`03-write`): pending");
  });

  it("lists active workflows when no id is given", () => {
    const state = startDraft();

    expect(handleQueryWorkflow(deps)).toContain(`ID: \`${state.id}\``);

    handleEndWorkflow(deps, state.id, "abort");

    expect(handleQueryWorkflow(deps)).toBe("No active workflows.");
  });
});

describe("handleEndWorkflow", () => {
  it("aborts an active workflow and cleans up its state", () => {
    const state = startDraft();

    const response = handleEndWorkflow(deps, state.id, "abort");

    expect(response).toContain("Workflow **draft** aborted.");
    expect(repository.get(state.id)).toBeNull();
    expect(existsSync(state.scratchpadPath)).toBe(false);
  });

  it("rejects unknown workflow ids", () => {
    expect(() => handleEndWorkflow(deps, "missing-id", "abort")).toThrow(
      /not found or no longer active/,
    );
  });
});

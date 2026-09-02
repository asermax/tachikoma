import { existsSync, readdirSync } from "node:fs";
import { mkdir, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { eq } from "drizzle-orm";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { findWorkflow } from "../../src/extensions/workflows/loader.ts";
import { WorkflowStateRepository } from "../../src/extensions/workflows/repository.ts";
import { workflowStates } from "../../src/extensions/workflows/schema.ts";
import {
  handleEndWorkflow,
  handleQueryWorkflow,
  handleStartWorkflow,
  handleUpdateWorkflowState,
  registerWorkflowTools,
  type WorkflowToolDeps,
} from "../../src/extensions/workflows/tools.ts";
import { createFakeLog, createTestDatabase, writeWorkflowFixture } from "./helpers.ts";

const log = createFakeLog();

let deps: WorkflowToolDeps;
let repository: WorkflowStateRepository;
let skillsRoot: string;
let db: AppDatabase;

beforeEach(async () => {
  skillsRoot = await mkdtemp(join(tmpdir(), "tachi-workflows-skills-"));

  await writeWorkflowFixture(skillsRoot, "writing", "draft", [
    { id: "01-plan", frontmatter: "title: Plan", body: "Sketch the outline." },
    { id: "02-research", frontmatter: "title: Research\nrequired: false" },
    { id: "03-write", frontmatter: "title: Write" },
  ]);

  db = await createTestDatabase();
  repository = new WorkflowStateRepository(db);

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

  it("rejects a second start while an instance is active, directing stale-instance recovery", () => {
    const state = startDraft();

    let message = "";
    try {
      handleStartWorkflow(deps, "writing", "draft");
    } catch (error) {
      message = (error as Error).message;
    }

    expect(message).toMatch(new RegExp(`already active.*${state.id}`, "s"));
    expect(message).toContain(`query_workflow(workflow_id="${state.id}")`);
    expect(message).toContain("scratchpad");
    expect(message).toContain("resume it from its current step");
    expect(message).toContain("surface the interrupted work");
    expect(message).toMatch(/discard/);
    expect(message).not.toContain("complete or abort");
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

// ---- composition (composes) ----------------------------------------------------

const startTop = (skill: string, workflow: string) => {
  handleStartWorkflow(deps, skill, workflow);
  const state = repository.getActive(skill, workflow);

  if (state == null) throw new Error("workflow was not persisted");

  return state;
};

describe("composition (composes)", () => {
  beforeEach(async () => {
    await writeWorkflowFixture(skillsRoot, "review", "outer", [
      { id: "01-prep", frontmatter: "title: Prep" },
      { id: "02-sub", frontmatter: "title: Sub\ncomposes: inner" },
      { id: "03-wrap", frontmatter: "title: Wrap" },
    ]);
    await writeWorkflowFixture(skillsRoot, "review", "inner", [
      { id: "01-a", frontmatter: "title: A", body: "Do A." },
      { id: "02-b", frontmatter: "title: B" },
    ]);
  });

  it("descends into the child when a composition step activates", () => {
    const state = startTop("review", "outer");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "start");

    const response = handleUpdateWorkflowState(deps, state.id, "01-prep", "complete");

    expect(response).toContain("Next step **A** (`01-a`) started.");
    expect(response).toContain("Do A.");
    expect(response).toContain("outer/02-sub > inner/01-a");

    // The child layer is hidden from the active listing.
    expect(handleQueryWorkflow(deps)).toContain("**outer**");
    expect(handleQueryWorkflow(deps)).not.toContain("**inner**");
  });

  it("auto-completes the parent step and resumes the parent when the child finishes", () => {
    const state = startTop("review", "outer");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "start");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "complete"); // descends, 01-a started
    handleUpdateWorkflowState(deps, state.id, "01-a", "complete"); // 02-b started

    const resumed = handleUpdateWorkflowState(deps, state.id, "02-b", "complete");

    expect(resumed).toContain("Next step **Wrap** (`03-wrap`) started.");

    const done = handleUpdateWorkflowState(deps, state.id, "03-wrap", "complete");

    expect(done).toContain("Workflow complete and finalized!");
    expect(done).toContain("3 completed, 0 skipped");
    expect(repository.get(state.id)).toBeNull();
  });

  it("renders the nested child path in the query view", () => {
    const state = startTop("review", "outer");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "start");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "complete");

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("outer/02-sub > inner/01-a");
    expect(view).toContain("### Active Child: inner");
    expect(view).toContain("**A** (`01-a`): started");
  });

  it("rejects operating on a composed child id directly", () => {
    const state = startTop("review", "outer");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "start");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "complete");

    const child = repository.getActiveChild(state.id);

    if (child == null) throw new Error("expected an active child");

    expect(() => handleUpdateWorkflowState(deps, child.id, "01-a", "complete")).toThrow(
      /composed child/,
    );
    expect(() => handleEndWorkflow(deps, child.id, "abort")).toThrow(/composed child/);
  });

  it("tears down the whole stack on abort", () => {
    const state = startTop("review", "outer");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "start");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "complete");

    const child = repository.getActiveChild(state.id);
    const response = handleEndWorkflow(deps, state.id, "abort");

    expect(response).toContain("2 records cleaned up");
    expect(repository.get(state.id)).toBeNull();
    expect(child != null && repository.get(child.id)).toBeNull();
  });
});

// ---- loop steps ----------------------------------------------------------------

describe("loop steps", () => {
  beforeEach(async () => {
    await writeWorkflowFixture(skillsRoot, "batch", "process-all", [
      { id: "01-collect", frontmatter: "title: Collect" },
      { id: "02-each", frontmatter: "title: Each\nloop: handle-one" },
      { id: "03-report", frontmatter: "title: Report" },
    ]);
    await writeWorkflowFixture(skillsRoot, "batch", "handle-one", [
      { id: "01-do", frontmatter: "title: Do", body: "Handle the item." },
    ]);
  });

  const advanceToLoopHalt = () => {
    const state = startTop("batch", "process-all");
    handleUpdateWorkflowState(deps, state.id, "01-collect", "start");
    const halt = handleUpdateWorkflowState(deps, state.id, "01-collect", "complete");

    return { state, halt };
  };

  it("halts auto-advance at a loop step", () => {
    const { halt } = advanceToLoopHalt();

    expect(halt).toContain("is a loop step");
    expect(halt).toContain("items=[...]");
  });

  it("iterates the target once per item then resumes the parent", () => {
    const { state } = advanceToLoopHalt();

    const first = handleUpdateWorkflowState(deps, state.id, "02-each", "start", ["x", "y"]);
    expect(first).toContain("Next step **Do** (`01-do`) started.");
    expect(first).toContain("process-all/02-each > handle-one/01-do (item: x)");

    const second = handleUpdateWorkflowState(deps, state.id, "01-do", "complete");
    expect(second).toContain("(item: y)");

    const resumed = handleUpdateWorkflowState(deps, state.id, "01-do", "complete");
    expect(resumed).toContain("Next step **Report** (`03-report`) started.");

    const done = handleUpdateWorkflowState(deps, state.id, "03-report", "complete");
    expect(done).toContain("3 completed, 0 skipped");
  });

  it("shows the loop block with the current iteration in the query view", () => {
    const { state } = advanceToLoopHalt();
    handleUpdateWorkflowState(deps, state.id, "02-each", "start", ["x", "y"]);

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("### Loop step: Each (`02-each`)");
    expect(view).toContain("Items (2): `x`, `y`");
    expect(view).toContain("Current iteration: 1 / 2");
    expect(view).toContain("Current item: `x`");
  });

  it("auto-completes a loop step with zero iterations", () => {
    const { state } = advanceToLoopHalt();

    const response = handleUpdateWorkflowState(deps, state.id, "02-each", "start", []);

    expect(response).toContain("Next step **Report** (`03-report`) started.");
    expect(repository.get(state.id)?.stepStates["02-each"]).toBe("completed");
  });

  it("validates the items parameter", () => {
    const { state } = advanceToLoopHalt();

    expect(() => handleUpdateWorkflowState(deps, state.id, "02-each", "start")).toThrow(
      /items parameter is required/,
    );

    const fresh = startTop("writing", "draft");
    expect(() => handleUpdateWorkflowState(deps, fresh.id, "01-plan", "start", ["a"])).toThrow(
      /not allowed when starting a non-loop step/,
    );

    // The items-on-non-start guard runs after transition validation, so the
    // step must be started before the guard can fire.
    handleUpdateWorkflowState(deps, fresh.id, "01-plan", "start");
    expect(() => handleUpdateWorkflowState(deps, fresh.id, "01-plan", "complete", ["a"])).toThrow(
      /only allowed on the 'start' action/,
    );
  });
});

// ---- condition steps -----------------------------------------------------------

describe("condition steps", () => {
  beforeEach(async () => {
    await writeWorkflowFixture(skillsRoot, "cond", "maybe", [
      { id: "01-first", frontmatter: "title: First" },
      { id: "02-gate", frontmatter: "title: Gate\ncondition: only when the inbox is non-empty" },
      { id: "03-last", frontmatter: "title: Last" },
    ]);
  });

  const advanceToCondition = () => {
    const state = startTop("cond", "maybe");
    handleUpdateWorkflowState(deps, state.id, "01-first", "start");
    const halt = handleUpdateWorkflowState(deps, state.id, "01-first", "complete");

    return { state, halt };
  };

  it("halts auto-advance at a condition step and surfaces the predicate", () => {
    const { halt } = advanceToCondition();

    expect(halt).toContain("has a condition to evaluate");
    expect(halt).toContain("only when the inbox is non-empty");
    expect(halt).toContain('action="start"');
    expect(halt).toContain('action="skip"');
  });

  it("starts the condition step when the agent decides it passes", () => {
    const { state } = advanceToCondition();

    const response = handleUpdateWorkflowState(deps, state.id, "02-gate", "start");

    expect(response).toContain("Step **Gate** (`02-gate`) started.");
    expect(repository.get(state.id)?.stepStates["02-gate"]).toBe("started");
  });

  it("skips a required condition step when the agent decides it fails", () => {
    const { state } = advanceToCondition();

    const response = handleUpdateWorkflowState(deps, state.id, "02-gate", "skip");

    expect(response).toContain("Next step **Last** (`03-last`) started.");
    expect(repository.get(state.id)?.stepStates["02-gate"]).toBe("skipped");
  });
});

// ---- edge cases ----------------------------------------------------------------

describe("handleStartWorkflow edge cases", () => {
  it("rejects a workflow with no steps", async () => {
    const emptyRoot = await mkdtemp(join(tmpdir(), "tachi-workflows-empty-"));
    await mkdir(join(emptyRoot, "hollow", "workflows", "void"), { recursive: true });

    const emptyDeps: WorkflowToolDeps = {
      ...deps,
      findWorkflow: (skill, workflow) => findWorkflow(emptyRoot, skill, workflow, log),
    };

    expect(() => handleStartWorkflow(emptyDeps, "hollow", "void")).toThrow(/has no steps/);
  });

  it("removes the scratchpad when persistence fails", () => {
    const failing = Object.create(repository) as WorkflowStateRepository;
    failing.create = () => {
      throw new Error("disk full");
    };
    failing.getActive = () => null;

    const failingDeps: WorkflowToolDeps = { ...deps, repository: failing };

    expect(() => handleStartWorkflow(failingDeps, "writing", "draft")).toThrow(/disk full/);
    expect(readdirSync(deps.scratchpadDir)).toHaveLength(0);
  });
});

describe("handleEndWorkflow completion label", () => {
  it("reports a single-record completion without a cleanup count", () => {
    handleStartWorkflow(deps, "writing", "draft");
    const state = repository.getActive("writing", "draft");

    if (state == null) throw new Error("workflow was not persisted");

    const response = handleEndWorkflow(deps, state.id, "complete");

    expect(response).toContain("Workflow **draft** completed.");
    expect(response).not.toContain("records cleaned up");
  });
});

describe("handleQueryWorkflow composed child and corruption", () => {
  const writeComposition = async () => {
    await writeWorkflowFixture(skillsRoot, "review", "outer", [
      { id: "01-prep", frontmatter: "title: Prep" },
      { id: "02-sub", frontmatter: "title: Sub\ncomposes: inner" },
    ]);
    await writeWorkflowFixture(skillsRoot, "review", "inner", [
      { id: "01-a", frontmatter: "title: A", body: "Do A." },
    ]);
  };

  const descendIntoChild = () => {
    handleStartWorkflow(deps, "review", "outer");
    const state = repository.getActive("review", "outer");

    if (state == null) throw new Error("workflow was not persisted");

    handleUpdateWorkflowState(deps, state.id, "01-prep", "start");
    handleUpdateWorkflowState(deps, state.id, "01-prep", "complete");

    return state;
  };

  it("explains how to reach a composed child queried by its own id", async () => {
    await writeComposition();
    const state = descendIntoChild();
    const child = repository.getActiveChild(state.id);

    if (child == null) throw new Error("expected an active child");

    const view = handleQueryWorkflow(deps, child.id);

    expect(view).toContain("This is a composed child");
    expect(view).toContain(`Parent workflow ID: \`${state.id}\``);
  });

  it("flags a composition step whose target is no longer registered", async () => {
    await writeComposition();
    const state = descendIntoChild();

    const corruptedDeps: WorkflowToolDeps = {
      ...deps,
      findWorkflow: (skill, workflow) =>
        workflow === "inner" ? null : findWorkflow(skillsRoot, skill, workflow, log),
    };

    const view = handleQueryWorkflow(corruptedDeps, state.id);

    expect(view).toContain("Workflow definition corruption detected");
    expect(view).toContain("references `review/inner`");
  });

  it("flags a composition step with a malformed composes value", () => {
    handleStartWorkflow(deps, "writing", "draft");
    const state = repository.getActive("writing", "draft");

    if (state == null) throw new Error("workflow was not persisted");

    db.update(workflowStates)
      .set({
        // A non-composes step alongside a started, malformed composes step exercises
        // both the skip-non-composes path and the resolve-throws path of detection.
        stepStates: { "01-plain": "completed", "02-bad": "started" },
        definitionSnapshot: [
          { id: "01-plain", title: "Plain", required: true, path: "/tmp/none" },
          { id: "02-bad", title: "Bad", required: true, path: "/tmp/none", composes: "a/b/c/" },
        ],
      })
      .where(eq(workflowStates.id, state.id))
      .run();

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("Workflow definition corruption detected");
    expect(view).toContain("references `a/b/c/`");
  });

  it("falls back to ids when rendering states with snapshot gaps", () => {
    handleStartWorkflow(deps, "writing", "draft");
    const state = repository.getActive("writing", "draft");

    if (state == null) throw new Error("workflow was not persisted");

    db.update(workflowStates)
      .set({
        // stepStates is missing entries for the snapshot steps, forcing the
        // pending fallback; the loop block reads a step absent from the snapshot.
        stepStates: {},
        loopState: { "99-ghost": { items: ["x"], index: 0 } },
      })
      .where(eq(workflowStates.id, state.id))
      .run();

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("**Plan** (`01-plan`): pending");
    expect(view).toContain("### Loop step: 99-ghost (`99-ghost`)");
  });
});

describe("handleEndWorkflow failure", () => {
  it("throws when the cascade deletes nothing despite a live record", () => {
    handleStartWorkflow(deps, "writing", "draft");
    const state = repository.getActive("writing", "draft");

    if (state == null) throw new Error("workflow was not persisted");

    const stubborn = Object.create(repository) as WorkflowStateRepository;
    stubborn.abortCascade = () => [];

    const stubbornDeps: WorkflowToolDeps = { ...deps, repository: stubborn };

    expect(() => handleEndWorkflow(stubbornDeps, state.id, "abort")).toThrow(
      /Failed to end workflow/,
    );
  });
});

describe("handleQueryWorkflow rendering branches", () => {
  it("throws when querying an unknown workflow id", () => {
    expect(() => handleQueryWorkflow(deps, "missing-id")).toThrow(/not found or no longer active/);
  });

  it("renders a started step with no instructions file using only the prefix", async () => {
    await writeWorkflowFixture(skillsRoot, "writing", "noinstr", [
      { id: "01-only", frontmatter: "title: Only" },
    ]);

    handleStartWorkflow(deps, "writing", "noinstr");
    const state = repository.getActive("writing", "noinstr");

    if (state == null) throw new Error("workflow was not persisted");

    db.update(workflowStates)
      .set({
        definitionSnapshot: [
          { id: "01-only", title: "Only", required: true, path: "/nonexistent-path" },
        ],
      })
      .where(eq(workflowStates.id, state.id))
      .run();

    const response = handleUpdateWorkflowState(deps, state.id, "01-only", "start");

    expect(response).toContain("Step **Only** (`01-only`) started.");
    expect(response).toContain("Step path: `/nonexistent-path`");
  });

  it("renders a completed loop block (all iterations done) in the query view", async () => {
    await writeWorkflowFixture(skillsRoot, "batch", "loopq", [
      { id: "01-each", frontmatter: "title: Each\nloop: handle" },
      { id: "02-end", frontmatter: "title: End" },
    ]);
    await writeWorkflowFixture(skillsRoot, "batch", "handle", [
      { id: "01-do", frontmatter: "title: Do", body: "Do it." },
    ]);

    handleStartWorkflow(deps, "batch", "loopq");
    const state = repository.getActive("batch", "loopq");

    if (state == null) throw new Error("workflow was not persisted");

    handleUpdateWorkflowState(deps, state.id, "01-each", "start", ["only"]);
    handleUpdateWorkflowState(deps, state.id, "01-do", "complete");

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("### Loop step: Each (`01-each`)");
    expect(view).toContain("1 / 1 (complete)");
  });

  it("renders the active loop child with its breadcrumb item during iteration", async () => {
    await writeWorkflowFixture(skillsRoot, "batch", "process", [
      { id: "01-each", frontmatter: "title: Each\nloop: handle" },
      { id: "02-end", frontmatter: "title: End" },
    ]);
    await writeWorkflowFixture(skillsRoot, "batch", "handle", [
      { id: "01-do", frontmatter: "title: Do", body: "Do it." },
      { id: "02-after", frontmatter: "title: After" },
    ]);

    handleStartWorkflow(deps, "batch", "process");
    const state = repository.getActive("batch", "process");

    if (state == null) throw new Error("workflow was not persisted");

    handleUpdateWorkflowState(deps, state.id, "01-each", "start", ["alpha", "beta"]);

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("(item: alpha)");
    expect(view).toContain("### Active Child: handle");
    expect(view).toContain("**Do** (`01-do`): started");
  });

  it("renders a zero-iteration loop block in the query view", async () => {
    await writeWorkflowFixture(skillsRoot, "batch", "loopz", [
      { id: "01-each", frontmatter: "title: Each\nloop: handle" },
      { id: "02-end", frontmatter: "title: End" },
    ]);
    await writeWorkflowFixture(skillsRoot, "batch", "handle", [
      { id: "01-do", frontmatter: "title: Do" },
    ]);

    handleStartWorkflow(deps, "batch", "loopz");
    const state = repository.getActive("batch", "loopz");

    if (state == null) throw new Error("workflow was not persisted");

    handleUpdateWorkflowState(deps, state.id, "01-each", "start", []);

    const view = handleQueryWorkflow(deps, state.id);

    expect(view).toContain("completed with zero iterations");
  });
});

describe("registerWorkflowTools", () => {
  const collectTools = () => {
    const tools = new Map<string, (toolCallId: string, params: unknown) => Promise<unknown>>();
    const defs = new Map<string, { description?: string; promptGuidelines?: string[] }>();
    const pi = {
      registerTool: vi.fn(
        (def: {
          name: string;
          description?: string;
          promptGuidelines?: string[];
          execute: (id: string, p: unknown) => Promise<unknown>;
        }) => {
          tools.set(def.name, def.execute);
          defs.set(def.name, {
            description: def.description,
            promptGuidelines: def.promptGuidelines,
          });
        },
      ),
    };

    registerWorkflowTools(pi as never, deps);

    return { tools, defs };
  };

  it("wires each handler through pi.registerTool", async () => {
    const { tools } = collectTools();

    expect([...tools.keys()].sort()).toEqual(
      ["end_workflow", "query_workflow", "start_workflow", "update_workflow_state"].sort(),
    );

    const started = (await tools.get("start_workflow")?.("c1", {
      skill_name: "writing",
      workflow_name: "draft",
    })) as { content: { text: string }[] };
    expect(started.content[0]?.text).toContain("Workflow started: **draft**");

    const state = repository.getActive("writing", "draft");

    if (state == null) throw new Error("workflow was not persisted");

    const updated = (await tools.get("update_workflow_state")?.("c2", {
      workflow_id: state.id,
      step: "01-plan",
      action: "start",
    })) as { content: { text: string }[] };
    expect(updated.content[0]?.text).toContain("Step **Plan** (`01-plan`) started.");

    const queried = (await tools.get("query_workflow")?.("c3", {})) as {
      content: { text: string }[];
    };
    expect(queried.content[0]?.text).toContain("Active Workflows");

    const ended = (await tools.get("end_workflow")?.("c4", {
      workflow_id: state.id,
      action: "abort",
    })) as { content: { text: string }[] };
    expect(ended.content[0]?.text).toContain("aborted");
  });

  it("carries stale-instance guidance on the agent-facing surfaces", () => {
    const { defs } = collectTools();

    const startGuidelines = (defs.get("start_workflow")?.promptGuidelines ?? []).join("\n");

    expect(startGuidelines).toContain("already active");
    expect(startGuidelines).toContain("query_workflow");
    expect(startGuidelines).toContain("never silently discard");

    const endDescription = defs.get("end_workflow")?.description ?? "";

    expect(endDescription).toContain("discard");
    expect(endDescription).toContain("stale run from an earlier session");
    expect(endDescription).toContain("surface");
  });
});

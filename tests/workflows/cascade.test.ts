import { describe, expect, it } from "vitest";

import {
  type CascadeDeps,
  getSnapshotStep,
  renderBreadcrumb,
  runCascade,
  stepToSnapshot,
  type UpdateAction,
  validateTransition,
} from "../../src/extensions/workflows/cascade.ts";
import type { StepDefinition, WorkflowDefinition } from "../../src/extensions/workflows/loader.ts";
import {
  STEP_STATES,
  type StepSnapshot,
  type StepStates,
} from "../../src/extensions/workflows/model.ts";
import type { WorkflowStateRecord } from "../../src/extensions/workflows/schema.ts";

interface StepSpec {
  id: string;
  title?: string;
  required?: boolean;
  condition?: string | null;
  composes?: string | null;
  loop?: string | null;
}

const makeSnapshot = (specs: StepSpec[]): StepSnapshot[] =>
  specs.map((spec) => ({
    id: spec.id,
    title: spec.title ?? spec.id,
    required: spec.required ?? true,
    path: `/skill/${spec.id}`,
    condition: spec.condition ?? null,
    composes: spec.composes ?? null,
    loop: spec.loop ?? null,
  }));

const makeDefinition = (
  skillName: string,
  workflowName: string,
  specs: StepSpec[],
): WorkflowDefinition => ({
  skillName,
  workflowName,
  path: `/skills/${skillName}/workflows/${workflowName}`,
  steps: specs.map((spec) => ({
    id: spec.id,
    title: spec.title ?? spec.id,
    instructionsPath: `/skills/${skillName}/workflows/${workflowName}/${spec.id}/instructions.md`,
    referencesPath: null,
    scriptsPath: null,
    required: spec.required ?? true,
    condition: spec.condition ?? null,
    composes: spec.composes ?? null,
    loop: spec.loop ?? null,
    properties: {},
  })),
});

interface RecordSpec {
  id: string;
  skillName?: string;
  workflowName?: string;
  parentWorkflowId?: string | null;
  parentStepId?: string | null;
  currentStep?: string | null;
  stepStates: StepStates;
  snapshot: StepSnapshot[];
  loopState?: WorkflowStateRecord["loopState"];
  scratchpadPath?: string;
}

const makeRecord = (spec: RecordSpec): WorkflowStateRecord => ({
  id: spec.id,
  skillName: spec.skillName ?? "writing",
  workflowName: spec.workflowName ?? "draft",
  parentWorkflowId: spec.parentWorkflowId ?? null,
  parentStepId: spec.parentStepId ?? null,
  currentStep: spec.currentStep ?? null,
  stepStates: spec.stepStates,
  definitionSnapshot: spec.snapshot,
  scratchpadPath: spec.scratchpadPath ?? "/pads/root.md",
  loopState: spec.loopState ?? null,
  deletedAt: null,
  createdAt: new Date(),
  updatedAt: new Date(),
});

const makeDeps = (
  chain: WorkflowStateRecord[],
  definitions: Map<string, WorkflowDefinition> = new Map(),
): CascadeDeps => ({
  repository: {
    getActiveChain: (rootId) => (chain[0]?.id === rootId ? chain : []),
  },
  findWorkflow: (skillName, workflowName) =>
    definitions.get(`${skillName}/${workflowName}`) ?? null,
});

const pendingStates = (snapshot: StepSnapshot[]): StepStates =>
  Object.fromEntries(snapshot.map((step) => [step.id, STEP_STATES.pending]));

describe("stepToSnapshot", () => {
  it("derives a snapshot from a step definition's directory", () => {
    const step: StepDefinition = {
      id: "01-plan",
      title: "Plan",
      instructionsPath: "/skills/writing/workflows/draft/01-plan/instructions.md",
      referencesPath: null,
      scriptsPath: null,
      required: false,
      condition: "when ready",
      composes: "sub",
      loop: null,
      properties: {},
    };

    expect(stepToSnapshot(step)).toEqual({
      id: "01-plan",
      title: "Plan",
      required: false,
      path: "/skills/writing/workflows/draft/01-plan",
      condition: "when ready",
      composes: "sub",
      loop: null,
    });
  });
});

describe("getSnapshotStep", () => {
  it("returns the matching step or null", () => {
    const snapshot = makeSnapshot([{ id: "a" }, { id: "b" }]);

    expect(getSnapshotStep(snapshot, "b")?.id).toBe("b");
    expect(getSnapshotStep(snapshot, "missing")).toBeNull();
  });
});

describe("validateTransition", () => {
  const snapshot = makeSnapshot([
    { id: "01", required: true },
    { id: "02", required: false },
    { id: "03", required: true, condition: "if relevant" },
  ]);

  const check = (states: StepStates, stepId: string, action: UpdateAction) =>
    validateTransition(states, stepId, action, snapshot);

  it("accepts a valid start", () => {
    expect(check({}, "01", "start")).toBeNull();
  });

  it("rejects an unknown step", () => {
    expect(check({}, "zzz", "start")).toContain("Invalid step 'zzz'");
  });

  it("rejects changing a completed or skipped step", () => {
    expect(check({ "01": "completed" }, "01", "complete")).toContain("already completed");
    expect(check({ "01": "skipped" }, "01", "start")).toContain("already skipped");
  });

  it("rejects starting a non-pending step", () => {
    expect(check({ "01": "started" }, "01", "start")).toContain("Can only start a pending step");
  });

  it("rejects completing a step that was never started", () => {
    expect(check({ "01": "pending" }, "01", "complete")).toContain("Must start a step");
  });

  it("rejects skipping a required step without a condition", () => {
    expect(check({}, "01", "skip")).toContain("required and cannot be skipped");
  });

  it("allows skipping a required step that carries a condition", () => {
    expect(check({}, "03", "skip")).toBeNull();
  });

  it("rejects skipping a step that is no longer pending", () => {
    expect(check({ "02": "started" }, "02", "skip")).toContain("Can only skip a pending step");
  });
});

describe("renderBreadcrumb", () => {
  it("renders only layers with a current step and only the deepest item", () => {
    expect(
      renderBreadcrumb([
        { workflowName: "draft", stepId: "01-plan", item: null },
        { workflowName: "sub", stepId: null, item: "ignored" },
        { workflowName: "iter", stepId: "01-each", item: "apple" },
      ]),
    ).toBe("draft/01-plan > iter/01-each (item: apple)");
  });

  it("returns an empty string when no layer has a current step", () => {
    expect(renderBreadcrumb([{ workflowName: "draft", stepId: null, item: null }])).toBe("");
  });
});

describe("runCascade routing failures", () => {
  it("throws when the workflow is not found", () => {
    const deps = makeDeps([]);

    expect(() => runCascade(deps, "missing", "01", "start")).toThrow(/not found or no longer/);
  });

  it("throws when addressing a composed child directly", () => {
    const snapshot = makeSnapshot([{ id: "01" }]);
    const child = makeRecord({
      id: "child",
      parentWorkflowId: "root",
      parentStepId: "01",
      stepStates: pendingStates(snapshot),
      snapshot,
    });

    expect(() => runCascade(makeDeps([child]), "child", "01", "start")).toThrow(
      /is a composed child/,
    );
  });

  it("throws when the step is not in the deepest layer", () => {
    const snapshot = makeSnapshot([{ id: "01" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    expect(() => runCascade(makeDeps([root]), "root", "nope", "start")).toThrow(
      /Invalid step 'nope'/,
    );
  });

  it("propagates a transition validation error", () => {
    const snapshot = makeSnapshot([{ id: "01" }]);
    const root = makeRecord({
      id: "root",
      stepStates: { "01": "completed" },
      snapshot,
    });

    expect(() => runCascade(makeDeps([root]), "root", "01", "start")).toThrow(/already completed/);
  });

  it("rejects items on a non-start action", () => {
    const snapshot = makeSnapshot([{ id: "01" }]);
    const root = makeRecord({
      id: "root",
      stepStates: { "01": "started" },
      snapshot,
    });

    expect(() => runCascade(makeDeps([root]), "root", "01", "complete", ["x"])).toThrow(
      /items parameter is only allowed/,
    );
  });

  it("rejects items when starting a non-loop step", () => {
    const snapshot = makeSnapshot([{ id: "01" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    expect(() => runCascade(makeDeps([root]), "root", "01", "start", ["x"])).toThrow(
      /not allowed when starting a non-loop step/,
    );
  });

  it("requires items when starting a loop step", () => {
    const snapshot = makeSnapshot([{ id: "01", loop: "writing/iter" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    expect(() => runCascade(makeDeps([root]), "root", "01", "start")).toThrow(
      /items parameter is required/,
    );
  });
});

describe("runCascade plain steps", () => {
  it("starts a plain step and returns the active outcome", () => {
    const snapshot = makeSnapshot([{ id: "01" }, { id: "02" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    const result = runCascade(makeDeps([root]), "root", "01", "start");

    expect(result.outcome).toMatchObject({
      deepestLayerId: "root",
      activeStepId: "01",
      finalizedTopLevel: false,
    });
    expect(result.batch).toEqual([
      {
        kind: "update",
        layerId: "root",
        stepStates: { "01": "started", "02": "pending" },
        currentStep: "01",
      },
    ]);
    expect(result.breadcrumbParts).toEqual([{ workflowName: "draft", stepId: "01", item: null }]);
    expect(result.scratchpadPath).toBe("/pads/root.md");
  });

  it("completes a step and auto-advances to the next plain step", () => {
    const snapshot = makeSnapshot([{ id: "01" }, { id: "02" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started", "02": "pending" },
      snapshot,
    });

    const result = runCascade(makeDeps([root]), "root", "01", "complete");

    expect(result.outcome).toMatchObject({ activeStepId: "02", finalizedTopLevel: false });
    expect(result.deepestSnapshot).toBe(snapshot);
  });

  it("skips an optional step and auto-advances", () => {
    const snapshot = makeSnapshot([{ id: "01", required: false }, { id: "02" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    const result = runCascade(makeDeps([root]), "root", "01", "skip");

    expect(result.outcome.activeStepId).toBe("02");
  });

  it("finalizes the top-level workflow when the last step completes", () => {
    const snapshot = makeSnapshot([{ id: "01" }, { id: "02", required: false }]);
    const root = makeRecord({
      id: "root",
      currentStep: "02",
      stepStates: { "01": "completed", "02": "started" },
      snapshot,
    });

    const result = runCascade(makeDeps([root]), "root", "02", "complete");

    expect(result.outcome).toMatchObject({
      activeStepId: null,
      finalizedTopLevel: true,
      completedCount: 2,
      skippedCount: 0,
    });
    expect(result.breadcrumbParts).toEqual([]);
    expect(result.batch.some((m) => m.kind === "softDelete")).toBe(true);
  });

  it("halts auto-advance at a conditional step", () => {
    const snapshot = makeSnapshot([{ id: "01" }, { id: "02", condition: "if needed" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started", "02": "pending" },
      snapshot,
    });

    const result = runCascade(makeDeps([root]), "root", "01", "complete");

    expect(result.outcome).toMatchObject({
      activeStepId: "02",
      haltedAtConditionStep: "02",
      finalizedTopLevel: false,
    });
  });

  it("halts auto-advance at a loop step encountered while advancing", () => {
    const snapshot = makeSnapshot([{ id: "01" }, { id: "02", loop: "writing/iter" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started", "02": "pending" },
      snapshot,
    });

    const result = runCascade(makeDeps([root]), "root", "01", "complete");

    expect(result.outcome).toMatchObject({ activeStepId: "02", haltedAtLoopStep: "02" });
  });
});

describe("runCascade composition", () => {
  const childDef = makeDefinition("writing", "sub", [{ id: "c1" }]);
  const definitions = new Map([["writing/sub", childDef]]);

  it("starting a composes step spawns a child layer", () => {
    const snapshot = makeSnapshot([{ id: "01", composes: "sub" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    const result = runCascade(makeDeps([root], definitions), "root", "01", "start");

    const create = result.batch.find((m) => m.kind === "create");
    expect(create).toMatchObject({
      kind: "create",
      parentId: "root",
      parentStepId: "01",
      workflowName: "sub",
    });
    expect(result.outcome.activeStepId).toBe("c1");
    expect(result.breadcrumbParts.map((p) => p.stepId)).toEqual(["01", "c1"]);
  });

  it("auto-advancing into a composes step spawns its child", () => {
    const snapshot = makeSnapshot([{ id: "01" }, { id: "02", composes: "sub" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started", "02": "pending" },
      snapshot,
    });

    const result = runCascade(makeDeps([root], definitions), "root", "01", "complete");

    expect(result.batch.some((m) => m.kind === "create")).toBe(true);
    expect(result.outcome.activeStepId).toBe("c1");
  });

  it("completing a child's last step resumes the parent composition step", () => {
    const childSnapshot = makeSnapshot([{ id: "c1" }]);
    const rootSnapshot = makeSnapshot([{ id: "01", composes: "sub" }, { id: "02" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started", "02": "pending" },
      snapshot: rootSnapshot,
    });
    const child = makeRecord({
      id: "child",
      workflowName: "sub",
      parentWorkflowId: "root",
      parentStepId: "01",
      currentStep: "c1",
      stepStates: { c1: "started" },
      snapshot: childSnapshot,
    });

    const result = runCascade(makeDeps([root, child], definitions), "root", "c1", "complete");

    expect(result.outcome).toMatchObject({ deepestLayerId: "root", activeStepId: "02" });
    expect(result.batch.some((m) => m.kind === "softDelete" && m.layerId === "child")).toBe(true);
  });

  it("rejects an invalid composition value", () => {
    const snapshot = makeSnapshot([{ id: "01", composes: "  " }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    expect(() => runCascade(makeDeps([root], definitions), "root", "01", "start")).toThrow(
      /has an invalid value/,
    );
  });

  it("rejects a composition target that no longer exists", () => {
    const snapshot = makeSnapshot([{ id: "01", composes: "gone" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    expect(() => runCascade(makeDeps([root], definitions), "root", "01", "start")).toThrow(
      /no longer exists/,
    );
  });

  it("aborts when composition nesting exceeds the safe depth", () => {
    const chain: WorkflowStateRecord[] = [];

    for (let depth = 0; depth < 25; depth += 1) {
      const snapshot = makeSnapshot([{ id: "01", composes: "sub" }]);

      chain.push(
        makeRecord({
          id: `layer-${depth}`,
          workflowName: depth === 0 ? "draft" : "sub",
          parentWorkflowId: depth === 0 ? null : `layer-${depth - 1}`,
          parentStepId: depth === 0 ? null : "01",
          stepStates: depth === 24 ? pendingStates(snapshot) : { "01": "started" },
          currentStep: depth === 24 ? null : "01",
          snapshot,
        }),
      );
    }

    expect(() => runCascade(makeDeps(chain, definitions), "layer-0", "01", "start")).toThrow(
      /exceeded the safe depth/,
    );
  });

  it("rejects a composition target with no steps", () => {
    const emptyDefs = new Map([["writing/empty", makeDefinition("writing", "empty", [])]]);
    const snapshot = makeSnapshot([{ id: "01", composes: "empty" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    expect(() => runCascade(makeDeps([root], emptyDefs), "root", "01", "start")).toThrow(
      /has no steps/,
    );
  });
});

describe("runCascade loops", () => {
  const iterDef = makeDefinition("writing", "iter", [{ id: "i1" }]);
  const definitions = new Map([["writing/iter", iterDef]]);

  it("starting a loop step with zero items completes it and advances", () => {
    const snapshot = makeSnapshot([{ id: "01", loop: "iter" }, { id: "02" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    const result = runCascade(makeDeps([root], definitions), "root", "01", "start", []);

    expect(result.outcome.activeStepId).toBe("02");
    const update = result.batch.find((m) => m.kind === "update");
    expect(update).toMatchObject({ stepStates: { "01": "completed", "02": "pending" } });
  });

  it("starting a loop step with items spawns the first iteration child", () => {
    const snapshot = makeSnapshot([{ id: "01", loop: "iter" }]);
    const root = makeRecord({ id: "root", stepStates: pendingStates(snapshot), snapshot });

    const result = runCascade(makeDeps([root], definitions), "root", "01", "start", ["a", "b"]);

    const create = result.batch.find((m) => m.kind === "create");
    expect(create).toMatchObject({ kind: "create", parentStepId: "01", workflowName: "iter" });
    expect(result.outcome.activeStepId).toBe("i1");
    expect(result.breadcrumbParts.at(-1)?.item).toBe("a");
  });

  it("advances the loop to the next item when a child iteration finishes", () => {
    const childSnapshot = makeSnapshot([{ id: "i1" }]);
    const rootSnapshot = makeSnapshot([{ id: "01", loop: "iter" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started" },
      loopState: { "01": { items: ["a", "b"], index: 0 } },
      snapshot: rootSnapshot,
    });
    const child = makeRecord({
      id: "child",
      workflowName: "iter",
      parentWorkflowId: "root",
      parentStepId: "01",
      currentStep: "i1",
      stepStates: { i1: "started" },
      snapshot: childSnapshot,
    });

    const result = runCascade(makeDeps([root, child], definitions), "root", "i1", "complete");

    const create = result.batch.find((m) => m.kind === "create");
    expect(create).toBeDefined();
    expect(result.outcome.activeStepId).toBe("i1");
    expect(result.breadcrumbParts.at(-1)?.item).toBe("b");
  });

  it("completes the loop step when the final iteration finishes", () => {
    const childSnapshot = makeSnapshot([{ id: "i1" }]);
    const rootSnapshot = makeSnapshot([{ id: "01", loop: "iter" }, { id: "02" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started", "02": "pending" },
      loopState: { "01": { items: ["a"], index: 0 } },
      snapshot: rootSnapshot,
    });
    const child = makeRecord({
      id: "child",
      workflowName: "iter",
      parentWorkflowId: "root",
      parentStepId: "01",
      currentStep: "i1",
      stepStates: { i1: "started" },
      snapshot: childSnapshot,
    });

    const result = runCascade(makeDeps([root, child], definitions), "root", "i1", "complete");

    expect(result.outcome.activeStepId).toBe("02");
    expect(result.batch.some((m) => m.kind === "softDelete" && m.layerId === "child")).toBe(true);
  });

  it("falls back to the parent's currentItem when the loop entry has no live item", () => {
    const childSnapshot = makeSnapshot([{ id: "i1" }]);
    const rootSnapshot = makeSnapshot([{ id: "01", loop: "iter" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started" },
      loopState: { "01": { items: [], index: -1 } },
      snapshot: rootSnapshot,
    });
    const child = makeRecord({
      id: "child",
      workflowName: "iter",
      parentWorkflowId: "root",
      parentStepId: "01",
      currentStep: "i1",
      stepStates: { i1: "started" },
      snapshot: childSnapshot,
    });

    const result = runCascade(makeDeps([root, child], definitions), "root", "i1", "complete");

    expect(result.outcome.finalizedTopLevel).toBe(true);
  });

  it("derives the iteration item from the parent loop bookkeeping for the breadcrumb", () => {
    const childSnapshot = makeSnapshot([{ id: "i1" }, { id: "i2" }]);
    const rootSnapshot = makeSnapshot([{ id: "01", loop: "iter" }]);
    const root = makeRecord({
      id: "root",
      currentStep: "01",
      stepStates: { "01": "started" },
      loopState: { "01": { items: ["apple"], index: 0 } },
      snapshot: rootSnapshot,
    });
    const child = makeRecord({
      id: "child",
      workflowName: "iter",
      parentWorkflowId: "root",
      parentStepId: "01",
      currentStep: "i1",
      stepStates: { i1: "started", i2: "pending" },
      snapshot: childSnapshot,
    });

    const result = runCascade(makeDeps([root, child], definitions), "root", "i1", "complete");

    expect(result.outcome.activeStepId).toBe("i2");
    expect(result.breadcrumbParts.at(-1)?.item).toBe("apple");
  });
});

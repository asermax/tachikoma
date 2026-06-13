import { describe, expect, it } from "vitest";

import {
  detectCycles,
  refKey,
  resolveComposes,
  validateWorkflowGraph,
} from "../../src/extensions/workflows/composition.ts";
import type { StepDefinition, WorkflowDefinition } from "../../src/extensions/workflows/loader.ts";

const step = (id: string, edge?: { composes?: string; loop?: string }): StepDefinition => ({
  id,
  title: id,
  instructionsPath: `/skills/x/workflows/w/${id}/instructions.md`,
  referencesPath: null,
  scriptsPath: null,
  required: true,
  condition: null,
  composes: edge?.composes ?? null,
  loop: edge?.loop ?? null,
  properties: {},
});

const workflow = (
  skillName: string,
  workflowName: string,
  steps: StepDefinition[],
): WorkflowDefinition => ({ skillName, workflowName, steps, path: `/skills/${skillName}` });

const graph = (...defs: WorkflowDefinition[]): Map<string, WorkflowDefinition> =>
  new Map(defs.map((def) => [refKey(def.skillName, def.workflowName), def]));

describe("resolveComposes", () => {
  it("resolves a bare name against the parent skill", () => {
    expect(resolveComposes("process-note", "inbox")).toEqual({
      skillName: "inbox",
      workflowName: "process-note",
    });
  });

  it("resolves a skill-qualified reference", () => {
    expect(resolveComposes("other/process-note", "inbox")).toEqual({
      skillName: "other",
      workflowName: "process-note",
    });
  });

  it("rejects empty and malformed values", () => {
    expect(() => resolveComposes("", "inbox")).toThrow(/non-empty/);
    expect(() => resolveComposes("   ", "inbox")).toThrow(/non-empty/);
    expect(() => resolveComposes("a/b/c", "inbox")).toThrow(/Malformed/);
    expect(() => resolveComposes("/wf", "inbox")).toThrow(/Malformed/);
    expect(() => resolveComposes("skill/", "inbox")).toThrow(/Malformed/);
  });
});

describe("detectCycles", () => {
  it("finds a mutual cycle (A composes B, B composes A)", () => {
    const cycles = detectCycles(
      graph(
        workflow("s", "a", [step("01", { composes: "b" })]),
        workflow("s", "b", [step("01", { composes: "a" })]),
      ),
    );

    expect(cycles).toHaveLength(1);
    expect(new Set(cycles[0])).toEqual(new Set(["s/a", "s/b"]));
  });

  it("finds a self-loop", () => {
    const cycles = detectCycles(graph(workflow("s", "a", [step("01", { composes: "a" })])));

    expect(cycles).toEqual([["s/a"]]);
  });

  it("treats loop and composes edges as the same graph", () => {
    const cycles = detectCycles(
      graph(
        workflow("s", "a", [step("01", { loop: "b" })]),
        workflow("s", "b", [step("01", { composes: "a" })]),
      ),
    );

    expect(cycles).toHaveLength(1);
  });

  it("returns nothing for an acyclic graph", () => {
    expect(
      detectCycles(
        graph(
          workflow("s", "a", [step("01", { composes: "b" })]),
          workflow("s", "b", [step("01")]),
        ),
      ),
    ).toEqual([]);
  });
});

describe("validateWorkflowGraph", () => {
  it("accepts a valid composition graph", () => {
    const { rejected } = validateWorkflowGraph(
      graph(
        workflow("s", "parent", [step("01", { composes: "child" })]),
        workflow("s", "child", [step("01")]),
      ),
    );

    expect(rejected.size).toBe(0);
  });

  it("rejects a step declaring both composes and loop", () => {
    const { rejected, warnings } = validateWorkflowGraph(
      graph(workflow("s", "bad", [step("01", { composes: "x", loop: "y" })])),
    );

    expect(rejected.has("s/bad")).toBe(true);
    expect(warnings.join(" ")).toMatch(/both composes and loop/);
  });

  it("rejects a missing target and a zero-step target", () => {
    const { rejected } = validateWorkflowGraph(
      graph(
        workflow("s", "dangling", [step("01", { composes: "ghost" })]),
        workflow("s", "empty-target", [step("01", { composes: "empty" })]),
        workflow("s", "empty", []),
      ),
    );

    expect(rejected.has("s/dangling")).toBe(true);
    expect(rejected.has("s/empty-target")).toBe(true);
    // The zero-step workflow itself is not rejected — only parents pointing at it.
    expect(rejected.has("s/empty")).toBe(false);
  });

  it("cascades rejection from a target to its parent", () => {
    const { rejected } = validateWorkflowGraph(
      graph(
        workflow("s", "grandparent", [step("01", { composes: "parent" })]),
        workflow("s", "parent", [step("01", { composes: "ghost" })]),
      ),
    );

    expect(rejected.has("s/parent")).toBe(true);
    expect(rejected.has("s/grandparent")).toBe(true);
  });
});

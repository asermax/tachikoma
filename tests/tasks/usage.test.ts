import { describe, expect, it } from "vitest";

import { buildTasksUsage } from "../../src/extensions/tasks/usage.ts";

// R13: the goal's meaning and recommended "end state + stated check + invariants" structure is
// documented on every agent-facing surface. This guards the tasks usage section.
describe("buildTasksUsage", () => {
  const usage = buildTasksUsage("UTC");

  it("documents the goal concept and its recommended structure (R13)", () => {
    expect(usage).toContain("goal");
    expect(usage).toContain("end state");
    expect(usage).toContain("stated check");
    expect(usage).toContain("invariants");
    expect(usage).toContain("must NOT change");
  });

  it("documents that a background run self-declares its outcome via update_goal (R13)", () => {
    expect(usage).toContain("update_goal");
    expect(usage).toContain("completed");
    expect(usage).toContain("not_completable");
    expect(usage).toContain("evidence");
  });

  it("states the goal is optional and derived from the prompt when omitted (R1/R3)", () => {
    expect(usage).toMatch(/optional|omit/i);
    expect(usage).toMatch(/deriv/i);
  });
});

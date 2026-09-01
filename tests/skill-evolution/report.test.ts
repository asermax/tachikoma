import { describe, expect, it, vi } from "vitest";

import {
  DISPATCH_BACKGROUND_TASK_EVENT,
  type DispatchBackgroundTaskPayload,
} from "../../src/events.ts";
import { impactLogPath } from "../../src/extensions/skill-evolution/layout.ts";
import {
  DEFAULT_POST_WORK_PROMPT,
  REPORT_SOURCE,
  type ReportRunInput,
  reportRun,
} from "../../src/extensions/skill-evolution/report.ts";
import type { ImpactLogEntry } from "../../src/extensions/skill-evolution/store.ts";

const WORKSPACE = "/ws/tachikoma";

const entry = (branch: string): ImpactLogEntry => ({
  date: "2027-03-04",
  skill: "deploy",
  pattern: "deploy-env-flag.md",
  branch,
  tip: "abc123",
  description: "Add the --env flag to the deploy guidance",
  status: "proposed",
});

const run = (
  over: Partial<ReportRunInput> = {},
): {
  emit: ReturnType<typeof vi.fn>;
  payload: DispatchBackgroundTaskPayload | undefined;
  event: string | undefined;
} => {
  const emit = vi.fn();
  reportRun({
    emit,
    workspaceRoot: WORKSPACE,
    verified: [entry("skill-evolution/deploy-env-flag")],
    ...over,
  });

  const [event, payload] = emit.mock.calls[0] ?? [];

  return { emit, event, payload: payload as DispatchBackgroundTaskPayload | undefined };
};

describe("reportRun", () => {
  it("emits the dispatch event with the skill-evolution source", () => {
    const { emit, event, payload } = run();

    expect(emit).toHaveBeenCalledTimes(1);
    expect(event).toBe(DISPATCH_BACKGROUND_TASK_EVENT);
    expect(payload?.source).toBe(REPORT_SOURCE);
  });

  it("forwards the default notification-only prompt verbatim", () => {
    const { payload } = run();

    expect(payload?.prompt).toContain(DEFAULT_POST_WORK_PROMPT);
    // The default names the full spectrum — removals too, not only modifications.
    expect(payload?.prompt).toContain("skill-change proposals");
  });

  it("forwards a configured post-work prompt verbatim", () => {
    const configured = "Open pull requests for every verified proposal, then report the links.";
    const { payload } = run({ postWorkPrompt: configured });

    expect(payload?.prompt).toContain(configured);
  });

  it("inlines the run context: proposal count, patterns touched, log path, the branches", () => {
    const { payload } = run({
      verified: [entry("skill-evolution/deploy-env-flag"), entry("skill-evolution/deploy-2fa")],
    });

    expect(payload?.prompt).toContain("Proposals created: 2");
    expect(payload?.prompt).toContain("deploy-env-flag.md");
    expect(payload?.prompt).toContain(impactLogPath(WORKSPACE));
    expect(payload?.prompt).toContain("skill-evolution/deploy-env-flag");
    expect(payload?.prompt).toContain("skill-evolution/deploy-2fa");
  });

  it("sets an explicit goal naming the follow-up (skipping goal extraction)", () => {
    const { payload } = run({
      verified: [entry("skill-evolution/deploy-env-flag")],
    });

    expect(typeof payload?.goal).toBe("string");
    expect(payload?.goal).toContain("1 skill-evolution proposal");
    expect(payload?.goal).toContain("skill-evolution/deploy-env-flag");
  });

  it("pluralizes the goal for multiple proposals", () => {
    const { payload } = run({
      verified: [entry("skill-evolution/a"), entry("skill-evolution/b")],
    });

    expect(payload?.goal).toContain("2 skill-evolution proposals");
  });
});

import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ rmMock: vi.fn() }));

vi.mock("node:fs/promises", () => ({
  rm: (...args: unknown[]) => h.rmMock(...args),
}));

const { handleAskBranch } = await import("../../src/extensions/boundary/ask-branch.ts");
const { BRANCH_SUMMARY, BOOMERANG_STATE } = await import("../../src/sessions/trunk.ts");

import type { Logger } from "../../src/log.ts";

const fakeLog = Object.assign(
  { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  { child: () => fakeLog },
) as unknown as Logger;

const branchSummary = (
  id: string,
  branchId: string,
  originalLeafId: string,
  baseId: string | null,
) => ({
  type: "branch_summary" as const,
  id,
  fromId: baseId ?? "",
  summary: `summary of ${branchId}`,
  details: { customType: BRANCH_SUMMARY, branchId, originalLeafId, baseId },
});

const boomerang = (currentTopicBaseId: string | null) => ({
  type: "custom" as const,
  customType: BOOMERANG_STATE,
  id: "boom-1",
  data: { currentTopicBaseId, lastDecision: "shift", relatedBranchId: null },
});

/**
 * Two collapsed branches: topic-1 (sum-1) and topic-2 (sum-2). The live branch extends sum-2, so the
 * boomerang base is sum-2. Both collapsed branches are queryable — only their summaries are in context,
 * not their full conversations (leaf-1, leaf-2), including topic-2 whose summary IS the live base. The
 * live (un-collapsed) branch would be topic-3 and has no record, so it reports "no such branch".
 */
const makeTrunk = () => {
  const entries = [
    branchSummary("sum-1", "topic-1", "leaf-1", null),
    branchSummary("sum-2", "topic-2", "leaf-2", "sum-1"),
    boomerang("sum-2"),
  ];

  const byId = new Map(entries.map((e) => [e.id, e]));
  byId.set("leaf-1", { type: "message", id: "leaf-1" } as never);
  byId.set("leaf-2", { type: "message", id: "leaf-2" } as never);

  return {
    sessionFile: "/tmp/trunk.jsonl",
    sessionManager: {
      getEntries: () => entries,
      getEntry: (id: string) => byId.get(id),
    },
  } as unknown as AgentSession;
};

beforeEach(() => {
  h.rmMock.mockReset();
  h.rmMock.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("handleAskBranch", () => {
  it("reports no such branch for an unknown id without failing", async () => {
    const trunk = makeTrunk();
    const shadowFork = vi.fn();
    const branchFile = vi.fn();

    const result = await handleAskBranch(
      { getTrunkSession: () => trunk, shadowFork, branchFile, log: fakeLog },
      { branchId: "topic-99", question: "what happened?" },
    );

    expect(result).toContain("No such branch 'topic-99'");
    expect(branchFile).not.toHaveBeenCalled();
    expect(shadowFork).not.toHaveBeenCalled();
  });

  it("answers the most-recently collapsed branch whose summary is the live base", async () => {
    const trunk = makeTrunk();
    const branchFile = vi.fn(() => "/tmp/branch-topic-2.jsonl");
    const fork = {
      prompt: vi.fn().mockResolvedValue("  full-conversation detail  "),
      dispose: vi.fn().mockResolvedValue(undefined),
    };
    const shadowFork = vi.fn().mockResolvedValue(fork);

    const result = await handleAskBranch(
      { getTrunkSession: () => trunk, shadowFork, branchFile, log: fakeLog },
      { branchId: "topic-2", question: "what happened?" },
    );

    // topic-2's summary (sum-2) IS the live base, but only its summary — not its full conversation
    // (leaf-2) — is in context, so it must be queryable like any earlier branch (regression:
    // previously rejected as "the currently active branch — it is already in context").
    expect(branchFile).toHaveBeenCalledWith("/tmp/trunk.jsonl", "leaf-2");
    expect(shadowFork).toHaveBeenCalledWith("/tmp/branch-topic-2.jsonl", { tier: "searcher" });
    expect(fork.prompt).toHaveBeenCalledOnce();
    expect(result).toBe("full-conversation detail");
    expect(fork.dispose).toHaveBeenCalledOnce();
    expect(h.rmMock).toHaveBeenCalledWith("/tmp/branch-topic-2.jsonl", { force: true });
  });

  it("answers a prior branch by branching its originalLeafId off the trunk file, then deletes the temp file", async () => {
    const trunk = makeTrunk();
    const branchFile = vi.fn(() => "/tmp/branch-topic-1.jsonl");
    const fork = {
      prompt: vi.fn().mockResolvedValue("  the deploy succeeded  "),
      dispose: vi.fn().mockResolvedValue(undefined),
    };
    const shadowFork = vi.fn().mockResolvedValue(fork);

    const result = await handleAskBranch(
      { getTrunkSession: () => trunk, shadowFork, branchFile, log: fakeLog },
      { branchId: "topic-1", question: "did the deploy work?" },
    );

    // Branched from the trunk's own file (never the live session) at topic-1's original leaf.
    expect(branchFile).toHaveBeenCalledWith("/tmp/trunk.jsonl", "leaf-1");
    expect(shadowFork).toHaveBeenCalledWith("/tmp/branch-topic-1.jsonl", { tier: "searcher" });
    expect(fork.prompt).toHaveBeenCalledOnce();
    expect(result).toBe("the deploy succeeded");
    expect(fork.dispose).toHaveBeenCalledOnce();
    expect(h.rmMock).toHaveBeenCalledWith("/tmp/branch-topic-1.jsonl", { force: true });
  });

  it("reports no active trunk gracefully", async () => {
    const result = await handleAskBranch(
      { getTrunkSession: () => null, shadowFork: vi.fn(), branchFile: vi.fn(), log: fakeLog },
      { branchId: "topic-1", question: "?" },
    );

    expect(result).toContain("no conversation trunk is active");
  });
});

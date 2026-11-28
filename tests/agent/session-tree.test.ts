import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import {
  appendInContextEntry,
  appendState,
  type BranchSummaryDetails,
  branchWithSummary,
  checkpointHasTangent,
  collapseTangent,
  enumerateEntries,
  getBranchEntries,
  getEntry,
  getLeafId,
  reseatLeaf,
  sessionCreatedAt,
} from "../../src/agent/session-tree.ts";
import { BRANCH_SUMMARY } from "../../src/sessions/trunk.ts";

const makeSession = (overrides: Record<string, unknown> = {}) => {
  const sessionManager = {
    branchWithSummary: vi.fn(() => "summary-1"),
    getBranch: vi.fn(() => [{ id: "e1" }]),
    getEntries: vi.fn(() => [{ id: "e1" }, { id: "e2" }]),
    getEntry: vi.fn((id: string) => ({ id })),
    getLeafId: vi.fn(() => "leaf-9"),
    getHeader: vi.fn(() => ({ timestamp: "2026-06-13T09:00:00Z" })),
    branch: vi.fn(),
    appendCustomEntry: vi.fn(() => "custom-1"),
    appendCustomMessageEntry: vi.fn(() => "msg-1"),
    ...overrides,
  };
  return { sessionManager, refreshContext: vi.fn() } as unknown as AgentSession;
};

/** A declaration: the system-message entry pi replays a request's prompt sections and tools from. */
const declaration = (id: string): SessionEntry =>
  ({ type: "message", id, message: { role: "system" } }) as unknown as SessionEntry;

const firstTopic: BranchSummaryDetails = {
  customType: "branch-summary",
  branchId: "topic-1",
  originalLeafId: "leaf-9",
  baseId: null,
};

describe("session-tree helpers", () => {
  it("branchWithSummary always marks the entry as hook-generated", () => {
    const session = makeSession();
    const details: BranchSummaryDetails = {
      customType: "branch-summary",
      branchId: "topic-2",
      originalLeafId: "leaf-9",
      baseId: "base-1",
    };

    expect(branchWithSummary(session, "base-1", "summary text", details)).toBe("summary-1");
    expect(session.sessionManager.branchWithSummary).toHaveBeenCalledWith(
      "base-1",
      "summary text",
      details,
      true,
    );
  });

  it("roots a first summary under the session's system message, not as a parentless root", () => {
    const session = makeSession({
      getBranch: vi.fn(() => [declaration("sys-1"), { id: "turn-1" }]),
    });

    expect(branchWithSummary(session, null, "summary text", firstTopic)).toBe("summary-1");

    // Only the entry's parent changes. The details still say that no summary is above it.
    expect(session.sessionManager.branchWithSummary).toHaveBeenCalledWith(
      "sys-1",
      "summary text",
      firstTopic,
      true,
    );
    expect(firstTopic.baseId).toBeNull();
  });

  it("extends the base it is given even when the path carries a system message", () => {
    const session = makeSession({
      getBranch: vi.fn(() => [declaration("sys-1"), { id: "summary-1" }]),
    });
    const secondTopic: BranchSummaryDetails = {
      ...firstTopic,
      branchId: "topic-2",
      baseId: "summary-1",
    };

    branchWithSummary(session, "summary-1", "summary text", secondTopic);

    expect(session.sessionManager.branchWithSummary).toHaveBeenCalledWith(
      "summary-1",
      "summary text",
      secondTopic,
      true,
    );
  });

  it("writes a parentless root when the live path carries no system message", () => {
    const session = makeSession();

    branchWithSummary(session, null, "summary text", firstTopic);

    expect(session.sessionManager.branchWithSummary).toHaveBeenCalledWith(
      null,
      "summary text",
      firstTopic,
      true,
    );
  });

  it("delegates tree reads to the session manager", () => {
    const session = makeSession();

    expect(getBranchEntries(session, "leaf-9")).toEqual([{ id: "e1" }]);
    expect(session.sessionManager.getBranch).toHaveBeenCalledWith("leaf-9");
    expect(enumerateEntries(session)).toHaveLength(2);
    expect(getEntry(session, "e1")).toEqual({ id: "e1" });
    expect(getLeafId(session)).toBe("leaf-9");
  });

  it("reads the session creation instant from the header", () => {
    const session = makeSession();

    expect(sessionCreatedAt(session)).toBe("2026-06-13T09:00:00Z");
    expect(session.sessionManager.getHeader).toHaveBeenCalled();
  });

  it("re-seats the leaf via branch()", () => {
    const session = makeSession();
    reseatLeaf(session, "base-1");
    expect(session.sessionManager.branch).toHaveBeenCalledWith("base-1");
  });

  // A fake session can only show that the seam asks pi to re-sync, and when: what the re-sync does
  // to the transcript is covered against the real SDK in `session-tree-projection.test.ts`.
  it("branchWithSummary re-syncs the in-memory transcript once the leaf has moved", () => {
    const calls: string[] = [];
    const session = makeSession({
      branchWithSummary: vi.fn(() => {
        calls.push("move");
        return "summary-1";
      }),
    });
    vi.mocked(session.refreshContext).mockImplementation(() => {
      calls.push("re-sync");
    });

    branchWithSummary(session, null, "summary text", firstTopic);

    // Every step records itself once, so this pins the order and that the re-sync runs exactly once.
    // It reads the leaf the raw move left behind, so it has to run after the move.
    expect(calls).toEqual(["move", "re-sync"]);
  });

  it("reseatLeaf re-syncs the in-memory transcript", () => {
    const session = makeSession();

    reseatLeaf(session, "base-1");

    expect(session.refreshContext).toHaveBeenCalledTimes(1);
  });

  it("collapseTangent re-syncs the in-memory transcript (R5)", () => {
    const session = makeSession();

    collapseTangent(session, "checkpoint-1", "tangent summary", {
      tangentId: "tangent-1",
      originalLeafId: "leaf-9",
    });

    expect(session.refreshContext).toHaveBeenCalledTimes(1);
  });

  it("does not re-sync when the leaf move throws", () => {
    const throwing = (): never => {
      throw new Error("Entry missing-1 not found");
    };
    const collapsed = makeSession({ branchWithSummary: vi.fn(throwing) });
    const reseated = makeSession({ branch: vi.fn(throwing) });

    expect(() =>
      collapseTangent(collapsed, "missing-1", "tangent summary", {
        tangentId: "tangent-1",
        originalLeafId: "leaf-9",
      }),
    ).toThrow("Entry missing-1 not found");
    expect(() => reseatLeaf(reseated, "missing-1")).toThrow("Entry missing-1 not found");

    for (const session of [collapsed, reseated]) {
      expect(session.refreshContext).not.toHaveBeenCalled();
    }
  });

  it("collapseTangent delegates to branchWithSummary rooted at the checkpoint with tangent details (R5)", () => {
    const session = makeSession();

    const id = collapseTangent(session, "checkpoint-1", "tangent summary", {
      tangentId: "tangent-1",
      originalLeafId: "leaf-9",
    });

    expect(id).toBe("summary-1");
    expect(session.sessionManager.branchWithSummary).toHaveBeenCalledWith(
      "checkpoint-1",
      "tangent summary",
      {
        customType: BRANCH_SUMMARY,
        branchId: "tangent-1",
        kind: "tangent",
        originalLeafId: "leaf-9",
        baseId: "checkpoint-1",
      },
      true,
    );
  });

  it("checkpointHasTangent reports a tangent only when a real turn follows the checkpoint (KD2)", () => {
    // `setCheckpoint` appends a boomerang-state entry, which advances the leaf past the checkpoint
    // message — so the guard must look for a real message turn on the path, not compare leaf ids.
    const msg = (id: string): SessionEntry => ({ type: "message", id }) as unknown as SessionEntry;
    const infra = (id: string): SessionEntry => ({ type: "custom", id }) as unknown as SessionEntry;
    const withTangent = {
      sessionManager: { getBranch: () => [msg("cp"), infra("boomerang"), msg("t1")] },
    } as unknown as AgentSession;
    const empty = {
      sessionManager: { getBranch: () => [msg("cp"), infra("boomerang")] },
    } as unknown as AgentSession;

    expect(checkpointHasTangent(withTangent, "cp")).toBe(true);
    // Only the infrastructure marker follows the checkpoint — no real turn, so no tangent.
    expect(checkpointHasTangent(empty, "cp")).toBe(false);
  });

  it("appends out-of-context state and hidden in-context entries", () => {
    const session = makeSession();

    expect(appendState(session, "trunk-state", { v: 1 })).toBe("custom-1");
    expect(session.sessionManager.appendCustomEntry).toHaveBeenCalledWith("trunk-state", { v: 1 });

    expect(
      appendInContextEntry(session, "related-branch", "pointer", { branchId: "topic-1" }),
    ).toBe("msg-1");
    expect(session.sessionManager.appendCustomMessageEntry).toHaveBeenCalledWith(
      "related-branch",
      "pointer",
      false,
      { branchId: "topic-1" },
    );
  });
});

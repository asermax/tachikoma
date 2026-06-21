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

const makeSession = () => {
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
  };
  return { sessionManager } as unknown as AgentSession;
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

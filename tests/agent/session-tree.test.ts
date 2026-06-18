import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import {
  appendInContextEntry,
  appendState,
  type BranchSummaryDetails,
  branchWithSummary,
  enumerateEntries,
  getBranchEntries,
  getEntry,
  getLeafId,
  reseatLeaf,
} from "../../src/agent/session-tree.ts";

const makeSession = () => {
  const sessionManager = {
    branchWithSummary: vi.fn(() => "summary-1"),
    getBranch: vi.fn(() => [{ id: "e1" }]),
    getEntries: vi.fn(() => [{ id: "e1" }, { id: "e2" }]),
    getEntry: vi.fn((id: string) => ({ id })),
    getLeafId: vi.fn(() => "leaf-9"),
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

  it("re-seats the leaf via branch()", () => {
    const session = makeSession();
    reseatLeaf(session, "base-1");
    expect(session.sessionManager.branch).toHaveBeenCalledWith("base-1");
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

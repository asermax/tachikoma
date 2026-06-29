import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import { collapseLiveTopicBranch } from "../../src/agent/branch-collapse.ts";
import type { Logger } from "../../src/log.ts";
import { BRANCH_SUMMARY } from "../../src/sessions/trunk.ts";

const fakeLog: Logger = Object.assign(
  { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  { child: () => fakeLog },
) as unknown as Logger;

const message = (id: string, role: "user" | "assistant", text: string) => ({
  type: "message" as const,
  id,
  parentId: null,
  timestamp: "2026-06-15T00:00:00Z",
  message: { role, content: [{ type: "text", text }] },
});

const makeSession = (overrides: Partial<Record<string, unknown>> = {}) => {
  const branchCalls: Array<{ branchFromId: string | null; summary: string; details: unknown }> = [];
  const custom: Array<{ customType: string; data: unknown }> = [];

  const sessionManager = {
    getLeafId: () => "leaf-3",
    getBranch: () => [
      message("base", "assistant", "earlier summary base"),
      message("u1", "user", "fix the build"),
      message("a1", "assistant", "cleared the cache"),
    ],
    branchWithSummary: vi.fn((branchFromId: string | null, summary: string, details: unknown) => {
      branchCalls.push({ branchFromId, summary, details });
      return "summary-entry-9";
    }),
    // The core primitive must NOT write boomerang state (the boundary layer owns that), so this
    // records any stray custom append to assert it stays untouched.
    appendCustomEntry: vi.fn((customType: string, data: unknown) => {
      custom.push({ customType, data });
      return "custom-1";
    }),
    ...overrides,
  };

  return { session: { sessionManager } as unknown as AgentSession, branchCalls, custom };
};

describe("collapseLiveTopicBranch", () => {
  it("summarizes via side.complete and appends a topic branch_summary with correct details", async () => {
    const { session, branchCalls, custom } = makeSession();
    const side = { complete: vi.fn().mockResolvedValue("a concise summary") };

    const result = await collapseLiveTopicBranch(
      { side, log: fakeLog },
      {
        session,
        currentBaseId: "base",
        branchId: "topic-2",
        reason: "trunk close",
      },
    );

    expect(result).toEqual({ newBaseId: "summary-entry-9" });

    expect(side.complete).toHaveBeenCalledWith(
      expect.objectContaining({
        tier: "processor",
        user: expect.stringContaining("fix the build"),
      }),
    );

    expect(branchCalls).toHaveLength(1);
    expect(branchCalls[0]?.branchFromId).toBe("base");
    expect(branchCalls[0]?.summary).toBe("a concise summary");
    expect(branchCalls[0]?.details).toEqual({
      customType: BRANCH_SUMMARY,
      branchId: "topic-2",
      kind: "topic",
      originalLeafId: "leaf-3",
      baseId: "base",
      reason: "trunk close",
    });

    // Core collapse writes NO boomerang state — that is the boundary layer's concern.
    expect(custom).toHaveLength(0);
  });

  it("returns null and appends nothing when summarization throws (R11 degrade)", async () => {
    const { session, branchCalls, custom } = makeSession();
    const side = { complete: vi.fn().mockRejectedValue(new Error("model down")) };

    const result = await collapseLiveTopicBranch(
      { side, log: fakeLog },
      { session, currentBaseId: "base", branchId: "topic-2" },
    );

    expect(result).toBeNull();
    expect(branchCalls).toHaveLength(0);
    expect(custom).toHaveLength(0);
  });

  it("returns null when the session has no leaf", async () => {
    const { session } = makeSession({ getLeafId: () => null });
    const side = { complete: vi.fn().mockResolvedValue("summary") };

    const result = await collapseLiveTopicBranch(
      { side, log: fakeLog },
      { session, currentBaseId: "base", branchId: "topic-2" },
    );

    expect(result).toBeNull();
    expect(side.complete).not.toHaveBeenCalled();
  });
});

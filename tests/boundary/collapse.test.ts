import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import { collapseCurrentTopic } from "../../src/extensions/boundary/collapse.ts";
import type { Logger } from "../../src/log.ts";
import { BOOMERANG_STATE, BRANCH_SUMMARY } from "../../src/sessions/trunk.ts";

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
  const custom: Array<{ customType: string; data: unknown }> = [];
  const branchCalls: Array<{ branchFromId: string | null; summary: string; details: unknown }> = [];

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
    appendCustomEntry: vi.fn((customType: string, data: unknown) => {
      custom.push({ customType, data });
      return "custom-1";
    }),
    ...overrides,
  };

  return { session: { sessionManager } as unknown as AgentSession, custom, branchCalls };
};

describe("collapseCurrentTopic", () => {
  it("appends a branch_summary with correct details, advances base, and writes boomerang", async () => {
    const { session, custom, branchCalls } = makeSession();
    const side = { complete: vi.fn().mockResolvedValue("a concise summary") };

    const result = await collapseCurrentTopic(
      { side, log: fakeLog },
      {
        session,
        currentBaseId: "base",
        branchId: "topic-2",
        reason: "clear shift",
        lastExchange: "user: fix the build\nassistant: cleared the cache",
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
    expect(branchCalls[0].branchFromId).toBe("base");
    expect(branchCalls[0].summary).toBe("a concise summary");
    expect(branchCalls[0].details).toEqual({
      customType: BRANCH_SUMMARY,
      branchId: "topic-2",
      originalLeafId: "leaf-3",
      baseId: "base",
      reason: "clear shift",
      lastExchange: "user: fix the build\nassistant: cleared the cache",
    });

    const boomerang = custom.find((c) => c.customType === BOOMERANG_STATE);
    expect(boomerang?.data).toEqual({
      currentTopicBaseId: "summary-entry-9",
      lastDecision: "shift",
      relatedBranchId: null,
    });
  });

  it("returns null and appends nothing when summarization throws (R11 degrade)", async () => {
    const { session, custom, branchCalls } = makeSession();
    const side = { complete: vi.fn().mockRejectedValue(new Error("model down")) };

    const result = await collapseCurrentTopic(
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

    const result = await collapseCurrentTopic(
      { side, log: fakeLog },
      { session, currentBaseId: "base", branchId: "topic-2" },
    );

    expect(result).toBeNull();
    expect(side.complete).not.toHaveBeenCalled();
  });
});

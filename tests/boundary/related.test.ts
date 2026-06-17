import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import {
  findRelatedBranch,
  injectRelatedBranchContext,
  RELATED_BRANCH_CUSTOM_TYPE,
} from "../../src/extensions/boundary/related.ts";
import type { Logger } from "../../src/log.ts";
import type { BranchRecord } from "../../src/sessions/trunk.ts";

const fakeLog = Object.assign(
  { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  { child: () => fakeLog },
) as unknown as Logger;

const records: BranchRecord[] = [
  {
    branchId: "topic-1",
    originalLeafId: "leaf-1",
    baseId: null,
    summaryEntryId: "sum-1",
    lastExchange: "user: japan trip\nassistant: autumn is best",
  },
  {
    branchId: "topic-2",
    originalLeafId: "leaf-2",
    baseId: "sum-1",
    summaryEntryId: "sum-2",
    lastExchange: "user: build fails\nassistant: clear cache",
  },
];

const makeSession = () =>
  ({
    sessionManager: {
      getEntry: (id: string) =>
        id === "sum-1"
          ? { type: "branch_summary", id, summary: "Planning a trip to Japan" }
          : id === "sum-2"
            ? { type: "branch_summary", id, summary: "Debugging the TypeScript build" }
            : undefined,
      appendCustomMessageEntry: vi.fn(() => "injected-1"),
    },
  }) as unknown as AgentSession;

describe("findRelatedBranch", () => {
  it("returns the matched record when the matcher picks a valid branch id", async () => {
    const session = makeSession();
    const side = {
      classify: vi.fn().mockResolvedValue({ branchId: "topic-1", reason: "same trip" }),
    };

    const result = await findRelatedBranch(
      { side, log: fakeLog },
      { session, branchRecords: records, message: "back to that Japan trip" },
    );

    expect(result).toEqual(records[0]);
    expect(side.classify).toHaveBeenCalledWith(
      expect.objectContaining({
        tier: "classifier",
        user: expect.stringContaining("Planning a trip to Japan"),
      }),
    );
  });

  it("returns null when the matcher returns null", async () => {
    const session = makeSession();
    const side = { classify: vi.fn().mockResolvedValue({ branchId: null, reason: "unrelated" }) };

    const result = await findRelatedBranch(
      { side, log: fakeLog },
      { session, branchRecords: records, message: "something brand new" },
    );

    expect(result).toBeNull();
  });

  it("returns null without calling the matcher when there are no records", async () => {
    const session = makeSession();
    const side = { classify: vi.fn() };

    const result = await findRelatedBranch(
      { side, log: fakeLog },
      { session, branchRecords: [], message: "anything" },
    );

    expect(result).toBeNull();
    expect(side.classify).not.toHaveBeenCalled();
  });

  it("returns null when the matcher picks an unknown branch id", async () => {
    const session = makeSession();
    const side = { classify: vi.fn().mockResolvedValue({ branchId: "topic-99", reason: "?" }) };

    const result = await findRelatedBranch(
      { side, log: fakeLog },
      { session, branchRecords: records, message: "x" },
    );

    expect(result).toBeNull();
  });

  it("fails soft to null when the matcher throws", async () => {
    const session = makeSession();
    const side = { classify: vi.fn().mockRejectedValue(new Error("model down")) };

    const result = await findRelatedBranch(
      { side, log: fakeLog },
      { session, branchRecords: records, message: "x" },
    );

    expect(result).toBeNull();
  });
});

describe("injectRelatedBranchContext", () => {
  it("injects exactly one in-context pointer with the summary and last exchange", () => {
    const session = makeSession();
    const append = vi.mocked(session.sessionManager.appendCustomMessageEntry);

    injectRelatedBranchContext(session, records[0], fakeLog);

    expect(append).toHaveBeenCalledOnce();
    const [customType, content, display, details] = append.mock.calls[0];
    expect(customType).toBe(RELATED_BRANCH_CUSTOM_TYPE);
    expect(display).toBe(false);
    expect(details).toEqual({ branchId: "topic-1" });
    expect(content).toContain("Planning a trip to Japan");
    expect(content).toContain("user: japan trip\nassistant: autumn is best");
  });
});

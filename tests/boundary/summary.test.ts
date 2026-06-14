import { describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { SessionsApi } from "../../src/extensions/api.ts";
import { type Completer, createSummaryProcessor } from "../../src/extensions/boundary/summary.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { error: vi.fn() } as unknown as Logger;

const session = { id: 3, summary: "old summary" } as SessionRecord;

const fakeSessions = () => {
  const update = vi.fn();
  return { api: { update } as unknown as SessionsApi, update };
};

describe("rolling summary processor", () => {
  it("updates summary and last exchange from the side completion", async () => {
    const side: Completer = { complete: vi.fn().mockResolvedValue("  fresh summary \n") };
    const { api, update } = fakeSessions();

    await createSummaryProcessor(side, api, fakeLog).process({
      session,
      userText: "hello",
      assistantText: "hi there",
    });

    expect(update).toHaveBeenCalledWith(3, {
      summary: "fresh summary",
      lastExchange: "user: hello\nassistant: hi there",
    });
  });

  it("skips the update entirely on an empty assistant turn", async () => {
    const side: Completer = { complete: vi.fn() };
    const { api, update } = fakeSessions();

    await createSummaryProcessor(side, api, fakeLog).process({
      session,
      userText: "hello",
      assistantText: "   ",
    });

    expect(side.complete).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();
  });

  it("labels the previous summary as none when the session has no summary", async () => {
    const side: Completer = { complete: vi.fn().mockResolvedValue("fresh") };
    const { api } = fakeSessions();
    const emptySession = { id: 4, summary: null } as SessionRecord;

    await createSummaryProcessor(side, api, fakeLog).process({
      session: emptySession,
      userText: "hello",
      assistantText: "hi",
    });

    expect(side.complete).toHaveBeenCalledWith(
      expect.objectContaining({
        user: expect.stringContaining("Previous summary: (none)"),
      }),
    );
  });

  it("clips an over-long summary and exchange", async () => {
    const side: Completer = { complete: vi.fn().mockResolvedValue("s".repeat(700)) };
    const { api, update } = fakeSessions();

    await createSummaryProcessor(side, api, fakeLog).process({
      session,
      userText: "u".repeat(3000),
      assistantText: "a".repeat(3000),
    });

    const [, payload] = update.mock.calls[0];
    expect(payload.summary).toHaveLength(601);
    expect(payload.summary.endsWith("…")).toBe(true);
    expect(payload.lastExchange).toHaveLength(2001);
    expect(payload.lastExchange.endsWith("…")).toBe(true);
  });

  it("still records the exchange when summarization fails", async () => {
    const side: Completer = { complete: vi.fn().mockRejectedValue(new Error("nope")) };
    const { api, update } = fakeSessions();

    await createSummaryProcessor(side, api, fakeLog).process({
      session,
      userText: "hello",
      assistantText: "hi",
    });

    expect(update).toHaveBeenCalledWith(3, { lastExchange: "user: hello\nassistant: hi" });
  });
});

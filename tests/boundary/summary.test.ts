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

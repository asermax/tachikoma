import { describe, expect, it, vi } from "vitest";
import { textMessage } from "../../src/domain/message.ts";
import type { AppContext, InboundContext, InboundMiddleware } from "../../src/extensions/api.ts";
import commands from "../../src/extensions/commands/index.ts";

const fakeLog = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };

const setup = async () => {
  let middleware: InboundMiddleware | null = null;
  const deliver = vi.fn();
  const complete = vi.fn().mockResolvedValue("a summary");

  const app = {
    inbound: {
      use: (registered: InboundMiddleware) => {
        middleware = registered;
      },
    },
    channels: { deliver },
    agent: { side: { complete } },
    log: fakeLog,
  } as unknown as AppContext<never>;

  await commands.setup(app);

  return { middleware: middleware as unknown as InboundMiddleware, deliver, complete };
};

// A trunk session whose sessionManager records a branchWithSummary call (the collapse).
const makeTrunk = (hasAssistantTurnSinceBase: boolean): InboundContext["trunk"] => {
  const branchWithSummary = vi.fn().mockReturnValue("summary-id");
  const session = {
    sessionManager: {
      branchWithSummary,
      getBranch: () => [
        {
          id: "leaf",
          type: "message",
          message: { role: "assistant", content: [{ type: "text", text: "done" }] },
        },
      ],
      getLeafId: () => "leaf",
      getEntries: () => [],
      appendCustomEntry: vi.fn().mockReturnValue("c-1"),
    },
    // biome-ignore lint/suspicious/noExplicitAny: minimal trunk stub for the command path
  } as any;

  return {
    session,
    sessionFile: "/tmp/trunk.jsonl",
    currentBaseId: null,
    branchRecords: [],
    hasAssistantTurnSinceBase,
  };
};

describe("commands extension", () => {
  it("handles /new by collapsing the current topic and acknowledging", async () => {
    const { middleware, deliver, complete } = await setup();
    const next = vi.fn();
    const message = textMessage("telegram", "/new");
    const trunk = makeTrunk(true);

    await middleware(message, { trunk }, next);

    // The branch was summarized and collapsed onto the trunk.
    expect(complete).toHaveBeenCalledTimes(1);
    expect(trunk?.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
    expect(message.metadata.handled).toBe(true);
    // A synchronous command ack renders straight to the channel, never the queue.
    expect(deliver).toHaveBeenCalledWith(expect.objectContaining({ immediate: true }));
    expect(next).not.toHaveBeenCalled();
  });

  it("skips collapse on /new when the live branch has no assistant turn yet", async () => {
    const { middleware, deliver, complete } = await setup();
    const next = vi.fn();
    const message = textMessage("telegram", "/new");
    const trunk = makeTrunk(false);

    await middleware(message, { trunk }, next);

    expect(complete).not.toHaveBeenCalled();
    expect(trunk?.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(message.metadata.handled).toBe(true);
    expect(deliver).toHaveBeenCalledWith(expect.objectContaining({ immediate: true }));
  });

  it("passes other messages through", async () => {
    const { middleware, deliver } = await setup();
    const next = vi.fn();
    const message = textMessage("telegram", "hello");

    await middleware(message, { trunk: null }, next);

    expect(next).toHaveBeenCalledTimes(1);
    expect(message.metadata.handled).toBeUndefined();
    expect(deliver).not.toHaveBeenCalled();
  });
});

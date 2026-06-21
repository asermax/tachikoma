import { describe, expect, it, vi } from "vitest";
import { textMessage } from "../../src/domain/message.ts";
import type { AppContext, InboundMiddleware } from "../../src/extensions/api.ts";
import commands from "../../src/extensions/commands/index.ts";

const fakeLog = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };

const setup = async () => {
  let middleware: InboundMiddleware | null = null;
  const deliver = vi.fn();

  const app = {
    inbound: {
      use: (registered: InboundMiddleware) => {
        middleware = registered;
      },
    },
    channels: { deliver },
    log: fakeLog,
  } as unknown as AppContext<never>;

  await commands.setup(app);

  return { middleware: middleware as unknown as InboundMiddleware, deliver };
};

describe("commands extension", () => {
  // As of DLT-181 (R9) a bare "/new" enters the coordinator's pending-input flow (it prompts for the
  // new topic's first message), and "/new <arg>" is prefix-stripped at submit time into `forceNew`,
  // which the boundary extension honors. Neither reaches inbound middleware, so this extension is now a
  // pass-through — reserved for future channel-agnostic commands. The pending-input behavior itself is
  // covered in tests/coordinator-pending-input.test.ts.

  it("passes every message through without handling it", async () => {
    const { middleware, deliver } = await setup();
    const next = vi.fn();

    await middleware(textMessage("telegram", "/new"), { trunk: null }, next);

    expect(next).toHaveBeenCalledTimes(1);
    expect(deliver).not.toHaveBeenCalled();
  });

  it("does not collapse a bare /new (that now belongs to the coordinator pending-input flow)", async () => {
    const { middleware, deliver } = await setup();
    const next = vi.fn();
    const message = textMessage("telegram", "/new");

    await middleware(message, { trunk: null }, next);

    expect(message.metadata.handled).toBeUndefined();
    expect(deliver).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });
});

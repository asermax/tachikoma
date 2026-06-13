import { describe, expect, it, vi } from "vitest";
import { textMessage } from "../../src/domain/message.ts";
import type { AppContext, InboundMiddleware } from "../../src/extensions/api.ts";
import commands from "../../src/extensions/commands/index.ts";

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
  } as unknown as AppContext<never>;

  await commands.setup(app);

  return { middleware: middleware as unknown as InboundMiddleware, deliver };
};

describe("commands extension", () => {
  it("handles /new by closing the session and acknowledging", async () => {
    const { middleware, deliver } = await setup();
    const closeSession = vi.fn().mockResolvedValue(undefined);
    const next = vi.fn();
    const message = textMessage("repl", "/new");

    await middleware(message, { session: null, closeSession, resumeSession: vi.fn() }, next);

    expect(closeSession).toHaveBeenCalledTimes(1);
    expect(message.metadata.handled).toBe(true);
    expect(deliver).toHaveBeenCalled();
    expect(next).not.toHaveBeenCalled();
  });

  it("passes other messages through", async () => {
    const { middleware, deliver } = await setup();
    const next = vi.fn();
    const message = textMessage("repl", "hello");

    await middleware(
      message,
      { session: null, closeSession: vi.fn(), resumeSession: vi.fn() },
      next,
    );

    expect(next).toHaveBeenCalledTimes(1);
    expect(message.metadata.handled).toBeUndefined();
    expect(deliver).not.toHaveBeenCalled();
  });
});

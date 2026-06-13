import { describe, expect, it, vi } from "vitest";
import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { InboundMessage } from "../../src/domain/message.ts";
import type { AppContext, InboundContext, InboundMiddleware } from "../../src/extensions/api.ts";
import boundary from "../../src/extensions/boundary/index.ts";

const fakeLog = { info: vi.fn(), error: vi.fn(), debug: vi.fn() };

const captureMiddleware = (config: { enabled: boolean }) => {
  let middleware: InboundMiddleware | null = null;

  const listResumable = vi.fn().mockReturnValue([]);

  const app = {
    extensionConfig: { enabled: config.enabled, idleCloseSeconds: 0 },
    sessions: { onExchange: vi.fn(), listResumable, get: vi.fn() },
    agent: { side: { classify: vi.fn(), complete: vi.fn() } },
    inbound: {
      use: (registered: InboundMiddleware) => {
        middleware = registered;
      },
    },
    status: vi.fn(),
    log: fakeLog,
  } as unknown as AppContext<{ enabled: boolean; idleCloseSeconds: number }>;

  boundary.setup(app);

  return { middleware: middleware as unknown as InboundMiddleware, listResumable };
};

const forceNewMessage = (): InboundMessage => ({
  text: "fresh topic",
  channel: "test",
  receivedAt: new Date(),
  media: [],
  metadata: { forceNew: true },
});

const context = (
  session: SessionRecord | null,
): InboundContext & { closeSession: ReturnType<typeof vi.fn> } => ({
  session,
  closeSession: vi.fn().mockResolvedValue(undefined),
  resumeSession: vi.fn().mockResolvedValue(undefined),
});

describe("boundary /new force-new handling", () => {
  it("closes the active session and skips topic detection", async () => {
    const { middleware, listResumable } = captureMiddleware({ enabled: true });
    const ctx = context({ id: 7 } as SessionRecord);
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(forceNewMessage(), ctx, next);

    expect(ctx.closeSession).toHaveBeenCalledOnce();
    expect(listResumable).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("is a no-op close when there is no active session", async () => {
    const { middleware } = captureMiddleware({ enabled: true });
    const ctx = context(null);
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(forceNewMessage(), ctx, next);

    expect(ctx.closeSession).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("still forces a fresh session when topic detection is disabled", async () => {
    const { middleware, listResumable } = captureMiddleware({ enabled: false });
    const ctx = context({ id: 9 } as SessionRecord);
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(forceNewMessage(), ctx, next);

    expect(ctx.closeSession).toHaveBeenCalledOnce();
    expect(listResumable).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });
});

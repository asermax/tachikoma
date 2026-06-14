import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { InboundMessage } from "../../src/domain/message.ts";
import type { AppContext, InboundContext, InboundMiddleware } from "../../src/extensions/api.ts";

const detectBoundary = vi.fn();

vi.mock("../../src/extensions/boundary/detector.ts", () => ({
  detectBoundary: (...args: unknown[]) => detectBoundary(...args),
}));

const boundary = (await import("../../src/extensions/boundary/index.ts")).default;

const fakeLog = { info: vi.fn(), error: vi.fn(), debug: vi.fn() };

const session = (overrides: Partial<SessionRecord>): SessionRecord =>
  ({
    id: 1,
    channel: "test",
    piSessionFile: "/tmp/session.jsonl",
    summary: "a topic",
    lastExchange: null,
    createdAt: new Date(),
    closedAt: new Date(),
    lastResumedAt: null,
    postProcessingState: null,
    ...overrides,
  }) as SessionRecord;

const captureMiddleware = (overrides?: {
  enabled?: boolean;
  idleCloseSeconds?: number;
  resumable?: SessionRecord[];
  get?: ReturnType<typeof vi.fn>;
}) => {
  let middleware: InboundMiddleware | null = null;

  const get = overrides?.get ?? vi.fn();
  const status = vi.fn();
  const onExchange = vi.fn();

  const app = {
    extensionConfig: {
      enabled: overrides?.enabled ?? true,
      idleCloseSeconds: overrides?.idleCloseSeconds ?? 0,
    },
    sessions: {
      onExchange,
      listResumable: vi.fn().mockReturnValue(overrides?.resumable ?? []),
      get,
    },
    agent: { side: { classify: vi.fn(), complete: vi.fn() } },
    inbound: {
      use: (registered: InboundMiddleware) => {
        middleware = registered;
      },
    },
    status,
    log: fakeLog,
  } as unknown as AppContext<{ enabled: boolean; idleCloseSeconds: number }>;

  boundary.setup(app);

  return { middleware: middleware as unknown as InboundMiddleware, get, status, onExchange };
};

const message = (metadata: Record<string, unknown> = {}): InboundMessage => ({
  text: "hello",
  channel: "test",
  receivedAt: new Date(),
  media: [],
  metadata,
});

const context = (active: SessionRecord | null): InboundContext => ({
  session: active,
  closeSession: vi.fn().mockResolvedValue(undefined),
  resumeSession: vi.fn().mockResolvedValue(undefined),
});

beforeEach(() => {
  detectBoundary.mockReset();
  detectBoundary.mockResolvedValue({ decision: "continue" });
  fakeLog.info.mockClear();
});

describe("boundary routing", () => {
  it("skips detection for system-originated injections", async () => {
    const { middleware } = captureMiddleware();
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message({ boundary: "skip" }), context(session({})), next);

    expect(detectBoundary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("returns early when detection is disabled and no force-new", async () => {
    const { middleware } = captureMiddleware({ enabled: false });
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message(), context(session({})), next);

    expect(detectBoundary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("force-routes to an explicit reply-to session", async () => {
    const target = session({ id: 5, summary: "replied topic" });
    const { middleware, get, status } = captureMiddleware({ get: vi.fn().mockReturnValue(target) });
    const ctx = context(session({ id: 1 }));
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message({ resumeSessionId: 5 }), ctx, next);

    expect(get).toHaveBeenCalledWith(5);
    expect(ctx.resumeSession).toHaveBeenCalledWith(target);
    expect(status).toHaveBeenCalledWith("Switching to the conversation you replied to");
    expect(detectBoundary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("does not switch when the reply-to target is already active", async () => {
    const active = session({ id: 5 });
    const { middleware } = captureMiddleware({ get: vi.fn().mockReturnValue(active) });
    const ctx = context(active);
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message({ resumeSessionId: 5 }), ctx, next);

    expect(ctx.resumeSession).not.toHaveBeenCalled();
    expect(detectBoundary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("does not switch when the reply-to target is unknown", async () => {
    const { middleware } = captureMiddleware({ get: vi.fn().mockReturnValue(undefined) });
    const ctx = context(session({ id: 1 }));
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message({ resumeSessionId: 99 }), ctx, next);

    expect(ctx.resumeSession).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("returns early when there is nothing to compare against", async () => {
    const { middleware } = captureMiddleware({ resumable: [] });
    const ctx = context(session({ id: 1, summary: null }));
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message(), ctx, next);

    expect(detectBoundary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("closes the active session on a topic shift", async () => {
    detectBoundary.mockResolvedValue({ decision: "new" });
    const { middleware, status } = captureMiddleware();
    const ctx = context(session({ id: 1, summary: "active" }));
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message(), ctx, next);

    expect(ctx.closeSession).toHaveBeenCalledOnce();
    expect(status).toHaveBeenCalledWith("Topic shift — closing the previous session");
    expect(next).toHaveBeenCalledOnce();
  });

  it("does not close on a new decision when no session is active", async () => {
    detectBoundary.mockResolvedValue({ decision: "new" });
    const { middleware } = captureMiddleware({
      resumable: [session({ id: 2, summary: "other" })],
    });
    const ctx = context(null);
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message(), ctx, next);

    expect(ctx.closeSession).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("resumes a previous session when the target exists", async () => {
    const target = session({ id: 2, summary: "old topic" });
    detectBoundary.mockResolvedValue({ decision: "resume", resumeSessionId: 2 });
    const { middleware, status } = captureMiddleware({
      resumable: [target],
      get: vi.fn().mockReturnValue(target),
    });
    const ctx = context(session({ id: 1, summary: "active" }));
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message(), ctx, next);

    expect(ctx.resumeSession).toHaveBeenCalledWith(target);
    expect(status).toHaveBeenCalledWith("Resuming a previous conversation");
    expect(next).toHaveBeenCalledOnce();
  });

  it("does not resume when the target session is missing", async () => {
    detectBoundary.mockResolvedValue({ decision: "resume", resumeSessionId: 2 });
    const { middleware } = captureMiddleware({
      resumable: [session({ id: 2, summary: "old topic" })],
      get: vi.fn().mockReturnValue(undefined),
    });
    const ctx = context(session({ id: 1, summary: "active" }));
    const next = vi.fn().mockResolvedValue(undefined);

    await middleware(message(), ctx, next);

    expect(ctx.resumeSession).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("logs when topic detection is disabled at setup", () => {
    captureMiddleware({ enabled: false });

    expect(fakeLog.info).toHaveBeenCalledWith("boundary detection disabled by configuration");
  });

  it("registers the idle-close processor when idleCloseSeconds is positive", () => {
    const { onExchange } = captureMiddleware({ idleCloseSeconds: 900 });

    expect(
      onExchange.mock.calls.some(([processor]) => processor?.name === "idle-close-timer"),
    ).toBe(true);
  });

  it("does not register the idle-close processor when idleCloseSeconds is zero", () => {
    const { onExchange } = captureMiddleware({ idleCloseSeconds: 0 });

    expect(
      onExchange.mock.calls.some(([processor]) => processor?.name === "idle-close-timer"),
    ).toBe(false);
  });
});

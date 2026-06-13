import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { InboundMessage } from "../../src/domain/message.ts";
import type { AppContext, InboundContext, InboundMiddleware } from "../../src/extensions/api.ts";
import type { BoundaryInput, SessionCandidate } from "../../src/extensions/boundary/detector.ts";

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

const captureMiddleware = (resumable: SessionRecord[]) => {
  let middleware: InboundMiddleware | null = null;

  const app = {
    extensionConfig: { enabled: true, idleCloseSeconds: 0 },
    sessions: {
      onExchange: vi.fn(),
      listResumable: vi.fn().mockReturnValue(resumable),
      get: vi.fn(),
    },
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

  return middleware as unknown as InboundMiddleware;
};

const message = (): InboundMessage => ({
  text: "hello",
  channel: "test",
  receivedAt: new Date(),
  media: [],
  metadata: {},
});

const context = (active: SessionRecord | null): InboundContext => ({
  session: active,
  closeSession: vi.fn().mockResolvedValue(undefined),
  resumeSession: vi.fn().mockResolvedValue(undefined),
});

const capturedCandidates = (): SessionCandidate[] =>
  (detectBoundary.mock.calls[0]?.[1] as BoundaryInput).candidates;

beforeEach(() => {
  detectBoundary.mockReset();
  detectBoundary.mockResolvedValue({ decision: "continue" });
});

describe("boundary candidate filtering", () => {
  it("excludes sessions whose post-processing failed", async () => {
    const middleware = captureMiddleware([
      session({ id: 1, summary: "healthy", postProcessingState: { summarize: "completed" } }),
      session({ id: 2, summary: "broken", postProcessingState: { summarize: "failed" } }),
    ]);

    await middleware(message(), context(null), vi.fn().mockResolvedValue(undefined));

    expect(capturedCandidates().map((candidate) => candidate.id)).toEqual([1]);
  });

  it("excludes sessions without a summary", async () => {
    const middleware = captureMiddleware([
      session({ id: 1, summary: "kept" }),
      session({ id: 2, summary: null }),
    ]);

    await middleware(message(), context(null), vi.fn().mockResolvedValue(undefined));

    expect(capturedCandidates().map((candidate) => candidate.id)).toEqual([1]);
  });

  it("excludes the active session from its own candidates", async () => {
    const active = session({ id: 1, summary: "active topic" });
    const middleware = captureMiddleware([active, session({ id: 2, summary: "other" })]);

    await middleware(message(), context(active), vi.fn().mockResolvedValue(undefined));

    expect(capturedCandidates().map((candidate) => candidate.id)).toEqual([2]);
  });
});

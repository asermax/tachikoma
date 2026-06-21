import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import type { Channel, Exchange } from "../src/channels/types.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";
import type { InboundMessage } from "../src/domain/message.ts";
import { EventBus } from "../src/events.ts";
import { createRegistrations, type Registrations } from "../src/extensions/registrations.ts";
import type { Logger } from "../src/log.ts";
import { TrunkState } from "../src/sessions/trunk.ts";

const createFakeLog = () => {
  const log = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

interface FakeSession {
  sessionFile: string;
  dispose: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  prompt: ReturnType<typeof vi.fn>;
  steer: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
  systemPrompt: string;
  sessionManager: {
    getEntries: () => unknown[];
    getLeafId: () => string | null;
    getBranch: () => unknown[];
  };
}

const createSession = (): FakeSession => ({
  sessionFile: "/tmp/trunk.jsonl",
  dispose: vi.fn(),
  subscribe: vi.fn(() => () => {}),
  prompt: vi.fn().mockResolvedValue(undefined),
  steer: vi.fn().mockResolvedValue(undefined),
  abort: vi.fn().mockResolvedValue(undefined),
  systemPrompt: "you are a helpful assistant",
  sessionManager: {
    getEntries: () => [],
    getLeafId: () => null,
    getBranch: () => [],
  },
});

const createAgent = (session: FakeSession) =>
  ({ open: vi.fn().mockResolvedValue(session) }) as unknown as AgentManager;

const textMsg = (text: string, extra: Partial<InboundMessage> = {}): InboundMessage => ({
  text,
  channel: "test",
  receivedAt: new Date(),
  media: [],
  metadata: {},
  ...extra,
});

const drainExchange = async (exchange: Exchange): Promise<void> => {
  for await (const _ of exchange.events) {
    // consume the stream to completion, like a real channel renderer
  }
};

const createChannel = (overrides: Partial<Channel> = {}): Channel => ({
  name: "test",
  start: vi.fn(),
  respond: vi.fn(drainExchange),
  deliver: vi.fn(async () => {}),
  status: vi.fn(),
  stop: vi.fn(),
  ...overrides,
});

// A capture middleware that records every processed message (after the coordinator's submit-time
// normalization, so forceNew/queued flags are visible) and then calls next() so the agent still runs.
const captureMiddleware = (sink: InboundMessage[]): Registrations => {
  const regs = createRegistrations();
  regs.inboundMiddleware.push(async (message, _context, next) => {
    sink.push(message);
    await next();
  });
  return regs;
};

// A capture middleware that fully handles the message (records it, skips the agent turn) — for tests
// that only care about whether/what reached the middleware.
const captureAndHandleMiddleware = (sink: InboundMessage[]): Registrations => {
  const regs = createRegistrations();
  regs.inboundMiddleware.push(async (message) => {
    sink.push(message);
    message.metadata.handled = true;
  });
  return regs;
};

interface Harness {
  coordinator: Coordinator;
  status: ReturnType<typeof vi.fn>;
  session: FakeSession;
  stop: () => Promise<void>;
}

const startHarness = (
  db: AppDatabase,
  regs: Registrations = createRegistrations(),
  overrides: { ttlMs?: number; now?: () => Date } = {},
): Harness => {
  const log = createFakeLog();
  const trunkState = new TrunkState(new KeyValueState(db, "trunk"));
  const events = new EventBus(log);
  const session = createSession();
  const agent = createAgent(session);
  const coordinator = new Coordinator(
    trunkState,
    agent,
    regs,
    events,
    log,
    "UTC",
    overrides.now ?? (() => new Date()),
    overrides.ttlMs ?? 120_000,
  );
  const status = vi.fn();
  coordinator.attachChannel(createChannel({ status }));
  const controller = new AbortController();
  const loop = coordinator.run(controller.signal);
  return {
    coordinator,
    status,
    session,
    stop: async () => {
      controller.abort();
      await loop;
    },
  };
};

let db: AppDatabase;

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-pending-input-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Coordinator pending-input (R9)", () => {
  it("renders the pending prompt for a bare /new and starts no agent exchange", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, status, session, stop } = startHarness(
      db,
      captureAndHandleMiddleware(seen),
    );

    coordinator.submit(textMsg("/new"));

    // The non-LLM prompt is rendered through the channel-agnostic status surface.
    expect(status).toHaveBeenCalledWith("What's the first message for the new topic?");
    // Nothing was enqueued — the bare command never reached the agent or the middleware.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(seen).toHaveLength(0);
    expect(session.prompt).not.toHaveBeenCalled();

    await stop();
  });

  it("captures the next message as the /new argument and re-dispatches it as forceNew", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, stop } = startHarness(db, captureAndHandleMiddleware(seen));

    coordinator.submit(textMsg("/new")); // bare → pending
    coordinator.submit(textMsg("actually the topic")); // captured as the argument

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    // The argument was re-dispatched as "/new <arg>" → prefix-stripped to forceNew with the arg text.
    expect(seen[0]).toMatchObject({ text: "actually the topic", metadata: { forceNew: true } });

    await stop();
  });

  it("captures the argument for /queue and /skill too", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, status, stop } = startHarness(db, captureAndHandleMiddleware(seen));

    coordinator.submit(textMsg("/queue"));
    expect(status).toHaveBeenCalledWith("What should I queue for the next turn?");
    coordinator.submit(textMsg("do this later"));
    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ text: "do this later", metadata: { queued: true } });

    seen.length = 0;

    coordinator.submit(textMsg("/skill"));
    expect(status).toHaveBeenCalledWith("Which skill should I load?");
    // /skill is pi-native (no coordinator prefix-strip), so it reaches the middleware verbatim.
    coordinator.submit(textMsg("code-review"));
    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]?.text).toBe("/skill code-review");

    await stop();
  });

  it("clears the pending state when any other command arrives, then honors it", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, stop } = startHarness(db, captureAndHandleMiddleware(seen));

    coordinator.submit(textMsg("/new")); // bare → pending
    coordinator.submit(textMsg("/checkpoint")); // a different command cancels pending

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    // /checkpoint is not a pending arg-command, so it flows through untouched (not captured as /new arg).
    expect(seen[0]?.text).toBe("/checkpoint");
    expect(seen[0]?.metadata.forceNew).toBeUndefined();

    await stop();
  });

  it("re-enters pending-input when a different bare arg-command arrives during pending", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, status, stop } = startHarness(db, captureAndHandleMiddleware(seen));

    coordinator.submit(textMsg("/new")); // pending /new
    coordinator.submit(textMsg("/queue")); // cancels /new, re-enters pending for /queue

    expect(status).toHaveBeenLastCalledWith("What should I queue for the next turn?");
    // Neither bare command reached the middleware.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(seen).toHaveLength(0);

    await stop();
  });

  it("expires the pending state after the TTL (clearing setTimeout)", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, stop } = startHarness(db, captureAndHandleMiddleware(seen), {
      ttlMs: 40,
    });

    coordinator.submit(textMsg("/new")); // pending, armed with a 40ms TTL timer
    // Wait past the TTL so the clearing setTimeout fires.
    await new Promise((resolve) => setTimeout(resolve, 90));
    coordinator.submit(textMsg("a late message")); // no longer captured

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]?.text).toBe("a late message");
    expect(seen[0]?.metadata.forceNew).toBeUndefined();

    await stop();
  });

  it("expires the pending state via the stale-check when now() is advanced past the TTL", async () => {
    const seen: InboundMessage[] = [];
    // Start at noon so a small clock advance never crosses a day boundary.
    let clock = new Date("2026-06-01T12:00:00Z").getTime();
    const now = () => new Date(clock);
    const { coordinator, stop } = startHarness(db, captureAndHandleMiddleware(seen), {
      ttlMs: 5_000,
      now,
    });

    coordinator.submit(textMsg("/new")); // promptedAt = noon (no real time has passed)
    // Advance the clock past the TTL WITHOUT waiting real time — the real timer has not fired, so only
    // the stale-check at the top of submit() can clear the pending state.
    clock += 6_000;
    coordinator.submit(textMsg("a stale-then-fresh message"));

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]?.text).toBe("a stale-then-fresh message");
    expect(seen[0]?.metadata.forceNew).toBeUndefined();

    await stop();
  });

  it("does not capture a replayed message as a pending argument (replay bypasses submit)", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, session, stop } = startHarness(db, captureMiddleware(seen));

    coordinator.submit(textMsg("/new")); // pending /new
    // replay() routes through the inbox directly (origin: system), never submit() — so it is not
    // captured as the pending argument.
    coordinator.replay("replayed triggering message");

    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());
    expect(session.prompt.mock.calls[0]?.[0]).toBe("replayed triggering message");

    // The pending state survived: a follow-up message is still captured as the /new argument.
    seen.length = 0;
    coordinator.submit(textMsg("the real argument"));
    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ text: "the real argument", metadata: { forceNew: true } });

    await stop();
  });

  it("does not capture a system-origin message as a pending argument", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator, stop } = startHarness(db, captureMiddleware(seen));

    coordinator.submit(textMsg("/new")); // pending /new
    // A system-origin message (e.g. a queue digest) bypasses the intercept and is not captured.
    coordinator.submit(
      textMsg("system digest", { metadata: { origin: "system", boundary: "skip" } }),
    );

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]?.text).toBe("system digest");

    await stop();
  });
});

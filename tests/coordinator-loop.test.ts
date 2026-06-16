import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import type { Channel, Delivery, Exchange } from "../src/channels/types.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import type { InboundMessage } from "../src/domain/message.ts";
import { EventBus } from "../src/events.ts";
import { createRegistrations, type Registrations } from "../src/extensions/registrations.ts";
import type { Logger } from "../src/log.ts";
import { SessionRegistry } from "../src/sessions/registry.ts";

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
  sessionFile: string | null;
  dispose: ReturnType<typeof vi.fn>;
  messages: unknown[];
  subscribe: ReturnType<typeof vi.fn>;
  prompt: ReturnType<typeof vi.fn>;
  steer: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
}

const createSession = (overrides: Partial<FakeSession> = {}): FakeSession => ({
  sessionFile: "/tmp/active.jsonl",
  dispose: vi.fn(),
  messages: [],
  subscribe: vi.fn(() => () => {}),
  prompt: vi.fn().mockResolvedValue(undefined),
  steer: vi.fn().mockResolvedValue(undefined),
  abort: vi.fn().mockResolvedValue(undefined),
  ...overrides,
});

const createAgent = (session: FakeSession) =>
  ({
    open: vi.fn().mockResolvedValue(session),
  }) as unknown as AgentManager;

const textMsg = (text: string, extra: Partial<InboundMessage> = {}): InboundMessage => ({
  text,
  channel: "test",
  receivedAt: new Date(),
  media: [],
  metadata: {},
  ...extra,
});

const makeCoordinator = (
  db: AppDatabase,
  agent: AgentManager,
  regs: Registrations = createRegistrations(),
  now: () => Date = () => new Date(),
) => {
  const log = createFakeLog();
  const registry = new SessionRegistry(db);
  const events = new EventBus(log);
  const coordinator = new Coordinator(registry, agent, regs, events, log, now);

  return { coordinator, registry, events, log };
};

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
  stop: vi.fn(),
  ...overrides,
});

let db: AppDatabase;
let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-coordinator-loop-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Coordinator.submit normalization", () => {
  const captureMiddleware = (sink: InboundMessage[]): Registrations => {
    const regs = createRegistrations();
    regs.inboundMiddleware.push(async (message) => {
      sink.push(message);
      message.metadata.handled = true;
    });

    return regs;
  };

  it("strips the /queue prefix and flags the message as queued", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator } = makeCoordinator(
      db,
      createAgent(createSession()),
      captureMiddleware(seen),
    );
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("/queue do the thing"));

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ text: "do the thing", metadata: { queued: true } });

    controller.abort();
    await loop;
  });

  it("strips the /new prefix and flags the message as forceNew", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator } = makeCoordinator(
      db,
      createAgent(createSession()),
      captureMiddleware(seen),
    );
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("/new start over"));

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ text: "start over", metadata: { forceNew: true } });

    controller.abort();
    await loop;
  });

  it("passes a plain message through untouched", async () => {
    const seen: InboundMessage[] = [];
    const { coordinator } = makeCoordinator(
      db,
      createAgent(createSession()),
      captureMiddleware(seen),
    );
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hello"));

    await vi.waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]?.text).toBe("hello");
    expect(seen[0]?.metadata.queued).toBeUndefined();

    controller.abort();
    await loop;
  });
});

describe("Coordinator.submit steering", () => {
  it("steers a mid-exchange message into the live run instead of queuing it", async () => {
    const session = createSession({
      subscribe: vi.fn(() => () => {}),
      prompt: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            setTimeout(resolve, 0);
          }),
      ),
    });
    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(
      createChannel({
        respond: vi.fn(async (exchange: Exchange) => {
          coordinator.submit(textMsg("steer me"));
          for await (const _ of exchange.events) {
            // drain
          }
        }),
      }),
    );

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("first"));

    await vi.waitFor(() => expect(session.steer).toHaveBeenCalledTimes(1));
    expect(session.steer.mock.calls[0]?.[0]).toBe("steer me");

    controller.abort();
    await loop;
  });

  it("logs and drops a steer that rejects", async () => {
    const session = createSession({
      steer: vi.fn().mockRejectedValue(new Error("nope")),
      prompt: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            setTimeout(resolve, 0);
          }),
      ),
    });
    const { coordinator, log } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(
      createChannel({
        respond: vi.fn(async (exchange: Exchange) => {
          coordinator.submit(textMsg("steer me"));
          for await (const _ of exchange.events) {
            // drain
          }
        }),
      }),
    );

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("first"));

    await vi.waitFor(() => expect(log.error).toHaveBeenCalled());

    controller.abort();
    await loop;
  });

  it("does not steer a system-origin message even mid-exchange", async () => {
    const session = createSession({
      prompt: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            setTimeout(resolve, 0);
          }),
      ),
    });
    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(
      createChannel({
        respond: vi.fn(async (exchange: Exchange) => {
          coordinator.submit(textMsg("sys", { metadata: { origin: "system" } }));
          for await (const _ of exchange.events) {
            // drain
          }
        }),
      }),
    );

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("first"));

    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());
    controller.abort();
    await loop;

    expect(session.steer).not.toHaveBeenCalled();
  });
});

describe("Coordinator.handle full exchange", () => {
  it("opens a session, runs open hooks, responds, and runs exchange processors", async () => {
    const session = createSession({
      messages: [{ role: "assistant", content: [{ type: "text", text: "hi there" }] }],
    });
    const regs = createRegistrations();

    const openHook = vi.fn();
    regs.sessionOpenHooks.push(openHook);

    const processed: { userText: string; assistantText: string }[] = [];
    regs.exchangeProcessors.push({
      name: "rolling",
      process: async (ctx) => {
        processed.push({ userText: ctx.userText, assistantText: ctx.assistantText });
      },
    });

    const { coordinator, events } = makeCoordinator(db, createAgent(session), regs);
    const opened = vi.fn();
    events.on("session:opened", opened);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("question"));

    await vi.waitFor(() => expect(processed).toHaveLength(1));

    expect(openHook).toHaveBeenCalledTimes(1);
    expect(opened).toHaveBeenCalledWith({
      session: expect.anything(),
      resumed: false,
    });
    expect(processed[0]).toEqual({ userText: "question", assistantText: "hi there" });

    controller.abort();
    await loop;
  });

  it("isolates a failing session open hook and still completes the exchange", async () => {
    const regs = createRegistrations();
    regs.sessionOpenHooks.push(vi.fn().mockRejectedValue(new Error("hook boom")));

    const { coordinator, log } = makeCoordinator(db, createAgent(createSession()), regs);
    const respond = vi.fn(drainExchange);
    coordinator.attachChannel(createChannel({ respond }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    await vi.waitFor(() => expect(respond).toHaveBeenCalled());
    expect(log.error).toHaveBeenCalledWith(expect.anything(), "session open hook failed");

    controller.abort();
    await loop;
  });

  it("skips the agent turn when a middleware marks the message handled", async () => {
    const regs = createRegistrations();
    const middleware = vi.fn(async (message: InboundMessage) => {
      message.metadata.handled = true;
    });
    regs.inboundMiddleware.push(middleware);

    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session), regs);
    const respond = vi.fn(drainExchange);
    coordinator.attachChannel(createChannel({ respond }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("/command"));

    await vi.waitFor(() => expect(middleware).toHaveBeenCalled());

    expect(respond).not.toHaveBeenCalled();
    expect(session.prompt).not.toHaveBeenCalled();
    expect(coordinator.current()).toBeNull();

    controller.abort();
    await loop;
  });

  it("runs the middleware chain in order before reaching the agent", async () => {
    const order: string[] = [];
    const regs = createRegistrations();
    regs.inboundMiddleware.push(async (_m, _c, next) => {
      order.push("a-before");
      await next();
      order.push("a-after");
    });
    regs.inboundMiddleware.push(async (_m, _c, next) => {
      order.push("b");
      await next();
    });

    const { coordinator } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    await vi.waitFor(() => expect(order).toContain("a-after"));
    expect(order).toEqual(["a-before", "b", "a-after"]);

    controller.abort();
    await loop;
  });

  it("logs an exchange processor rejection without failing the loop", async () => {
    const regs = createRegistrations();
    regs.exchangeProcessors.push({
      name: "flaky",
      process: vi.fn().mockRejectedValue(new Error("processor boom")),
    });

    const { coordinator, log } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    await vi.waitFor(() =>
      expect(log.error).toHaveBeenCalledWith(
        expect.objectContaining({ processor: "flaky" }),
        "exchange processor failed",
      ),
    );

    controller.abort();
    await loop;
  });

  it("logs and recovers when an exchange throws", async () => {
    const regs = createRegistrations();
    regs.inboundMiddleware.push(async () => {
      throw new Error("middleware boom");
    });

    const { coordinator, log } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    await vi.waitFor(() =>
      expect(log.error).toHaveBeenCalledWith(expect.anything(), "exchange failed"),
    );

    controller.abort();
    await loop;
  });

  it("reuses the already-active session instead of opening a new one", async () => {
    const session = createSession();
    const agent = createAgent(session);
    const { coordinator } = makeCoordinator(db, agent);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("one"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    coordinator.submit(textMsg("two"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalledTimes(2));

    expect((agent.open as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(1);

    controller.abort();
    await loop;
  });
});

describe("Coordinator.status", () => {
  it("routes status through the channel during a normal exchange", () => {
    const { coordinator } = makeCoordinator(db, createAgent(createSession()));
    const status = vi.fn();
    coordinator.attachChannel(createChannel({ status }));

    coordinator.status("Gathering context…");

    expect(status).toHaveBeenCalledWith("Gathering context…");
  });

  it("swallows a channel status rendering error", () => {
    const { coordinator, log } = makeCoordinator(db, createAgent(createSession()));
    coordinator.attachChannel(
      createChannel({
        status: vi.fn(() => {
          throw new Error("render boom");
        }),
      }),
    );

    expect(() => coordinator.status("hi")).not.toThrow();
    expect(log.debug).toHaveBeenCalledWith(expect.anything(), "channel status rendering failed");
  });
});

describe("Coordinator.abortExchange", () => {
  it("aborts the active session's run", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));

    // No active session yet: should be a no-op.
    await coordinator.abortExchange();
    expect(session.abort).not.toHaveBeenCalled();

    coordinator.attachChannel(createChannel());
    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    await coordinator.abortExchange();
    expect(session.abort).toHaveBeenCalledTimes(1);

    controller.abort();
    await loop;
  });
});

describe("Coordinator.closeActiveSessionIfIdle", () => {
  it("returns false with no active session", async () => {
    const { coordinator } = makeCoordinator(db, createAgent(createSession()));
    expect(await coordinator.closeActiveSessionIfIdle()).toBe(false);
  });

  it("closes an idle active session and returns true", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    expect(await coordinator.closeActiveSessionIfIdle()).toBe(true);
    expect(coordinator.current()).toBeNull();
    expect(session.dispose).toHaveBeenCalled();

    controller.abort();
    await loop;
  });
});

describe("Coordinator.closeActiveSessionIfIdle post-processing status", () => {
  it("suppresses per-processor status on idle close but still runs them and logs", async () => {
    const regs = createRegistrations();
    const ran: string[] = [];
    regs.postProcessors.push({ name: "memory", process: async () => void ran.push("memory") });
    regs.postProcessors.push({
      name: "archive",
      phase: "finalize",
      process: async () => void ran.push("archive"),
    });

    const status = vi.fn();
    const { coordinator, registry, log } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel({ status }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());
    const sessionId = coordinator.current()?.id as number;

    const closed = await coordinator.closeActiveSessionIfIdle();

    expect(closed).toBe(true);
    // No Post-processing status line reached the channel…
    expect(status).not.toHaveBeenCalled();
    // …but every processor still ran…
    expect(ran).toEqual(["memory", "archive"]);
    // …and each line was logged for operators instead.
    expect(log.debug).toHaveBeenCalledWith(
      expect.objectContaining({ status: "Post-processing: memory…" }),
      "pipeline status",
    );

    expect(registry.get(sessionId)?.postProcessingState).toMatchObject({
      memory: "completed",
      archive: "completed",
    });

    controller.abort();
    await loop;
  });
});

describe("Coordinator.closeActiveSession post-processing status", () => {
  it("emits per-processor status lines on an explicit (non-idle) close", async () => {
    const regs = createRegistrations();
    regs.postProcessors.push({ name: "memory", process: async () => {} });

    const status = vi.fn();
    const { coordinator } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel({ status }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    await coordinator.closeActiveSession();

    expect(status).toHaveBeenCalledWith("Post-processing: memory…");

    controller.abort();
    await loop;
  });
});

describe("Coordinator.runPostProcessing", () => {
  it("runs processors by phase and records completed/failed state", async () => {
    const regs = createRegistrations();
    const calls: string[] = [];

    regs.postProcessors.push({
      name: "memory",
      phase: "main",
      process: async () => {
        calls.push("memory");
      },
    });
    regs.postProcessors.push({
      name: "archive",
      phase: "finalize",
      process: async () => {
        calls.push("archive");
      },
    });
    regs.postProcessors.push({
      name: "broken",
      phase: "main",
      process: vi.fn().mockRejectedValue(new Error("pp boom")),
    });

    const { coordinator, registry, events, log } = makeCoordinator(
      db,
      createAgent(createSession()),
      regs,
    );

    const record = registry.create("test", "/tmp/active.jsonl");

    const postProcessed = vi.fn();
    events.on("session:post-processed", postProcessed);

    coordinator.attachChannel(createChannel());

    // recoverUnprocessedSessions closes the open (null-state) record then runs the pipeline.
    // (The shouldSkip-for-completed wiring is covered in tests/post-processing.test.ts; recovery
    // only ever selects null-state records, so it always runs every registered processor.)
    await coordinator.recoverUnprocessedSessions();

    // main phase runs before finalize
    expect(calls.indexOf("memory")).toBeLessThan(calls.indexOf("archive"));

    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ processor: "broken" }),
      "post-processor failed",
    );

    const finalState = registry.get(record.id)?.postProcessingState;
    expect(finalState).toMatchObject({
      memory: "completed",
      archive: "completed",
      broken: "failed",
    });
    expect(postProcessed).toHaveBeenCalledTimes(1);
  });
});

describe("Coordinator.recoverUnprocessedSessions", () => {
  it("closes and post-processes sessions left open by a previous run (AC6)", async () => {
    const regs = createRegistrations();
    const processed: number[] = [];
    regs.postProcessors.push({
      name: "recover",
      process: async (ctx) => {
        if (ctx.session != null) processed.push(ctx.session.id);
      },
    });

    const { coordinator, registry } = makeCoordinator(db, createAgent(createSession()), regs);

    const dangling = registry.create("test", "/tmp/active.jsonl");

    await coordinator.recoverUnprocessedSessions();

    expect(processed).toEqual([dangling.id]);
    expect(registry.get(dangling.id)?.closedAt).not.toBeNull();
  });

  it("post-processes a closed-but-unprocessed session without re-closing it (AC5)", async () => {
    const regs = createRegistrations();
    const processed: number[] = [];
    regs.postProcessors.push({
      name: "recover",
      process: async (ctx) => {
        if (ctx.session != null) processed.push(ctx.session.id);
      },
    });

    const { coordinator, registry } = makeCoordinator(db, createAgent(createSession()), regs);

    // Closed by a crash/interrupted drain, but postProcessingState never persisted.
    const closedUnprocessed = registry.create("test", "/tmp/active.jsonl");
    const closed = registry.close(closedUnprocessed.id);
    const closedAtBefore = closed.closedAt;

    await coordinator.recoverUnprocessedSessions();

    expect(processed).toEqual([closedUnprocessed.id]);
    // closedAt must be untouched — recovery must not restamp an already-closed session.
    expect(registry.get(closedUnprocessed.id)?.closedAt).toEqual(closedAtBefore);
  });

  it("recovers both an open-dangling and a closed-but-unprocessed session in one pass", async () => {
    const regs = createRegistrations();
    const processed: number[] = [];
    regs.postProcessors.push({
      name: "recover",
      process: async (ctx) => {
        if (ctx.session != null) processed.push(ctx.session.id);
      },
    });

    const { coordinator, registry } = makeCoordinator(db, createAgent(createSession()), regs);

    const open = registry.create("test", "/tmp/open.jsonl"); // dangling: open, null state
    const closed = registry.create("test", "/tmp/closed.jsonl"); // interrupted: closed, null state
    registry.close(closed.id);

    await coordinator.recoverUnprocessedSessions();

    expect(processed).toHaveLength(2);
    expect(processed).toEqual(expect.arrayContaining([open.id, closed.id]));
    expect(registry.get(open.id)?.closedAt).not.toBeNull();
  });
});

describe("Coordinator.run shutdown sequence", () => {
  it("announces wrap-up and closes the active session via shutdownStatus", async () => {
    const session = createSession();
    const regs = createRegistrations();
    const ppCalls: string[] = [];
    regs.postProcessors.push({
      name: "finalizer",
      process: async () => {
        ppCalls.push("finalizer");
      },
    });

    const { coordinator } = makeCoordinator(db, createAgent(session), regs);
    const shutdownStatus = vi.fn(async () => {});
    coordinator.attachChannel(createChannel({ shutdownStatus }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    controller.abort();
    await loop;

    expect(shutdownStatus).toHaveBeenCalledWith("Wrapping up the conversation…");
    expect(shutdownStatus).toHaveBeenCalledWith("Done");
    expect(ppCalls).toContain("finalizer");
    expect(coordinator.current()).toBeNull();
  });

  it("falls back to the status surface for shutdown lines when shutdownStatus is absent", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    const status = vi.fn();
    coordinator.attachChannel(createChannel({ status, shutdownStatus: undefined }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    controller.abort();
    await loop;

    expect(status).toHaveBeenCalledWith("Wrapping up the conversation…");
    expect(status).toHaveBeenCalledWith("Done");
  });

  it("logs but does not throw when a shutdown status render fails", async () => {
    const session = createSession();
    const { coordinator, log } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(
      createChannel({
        shutdownStatus: vi.fn(async () => {
          throw new Error("ss boom");
        }),
      }),
    );

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    controller.abort();
    await loop;

    expect(log.debug).toHaveBeenCalledWith(expect.anything(), "shutdown status rendering failed");
  });

  it("skips the wrap-up announcement when there is no active session", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    const shutdownStatus = vi.fn(async () => {});
    coordinator.attachChannel(createChannel({ shutdownStatus }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;

    expect(shutdownStatus).not.toHaveBeenCalled();
  });

  it("isolates a failing shutdown hook", async () => {
    const regs = createRegistrations();
    regs.shutdownHooks.push({
      name: "bad",
      hook: vi.fn().mockRejectedValue(new Error("hook boom")),
    });

    const { coordinator, log } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;

    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ hook: "bad" }),
      "shutdown hook failed",
    );
  });

  it("logs a shutdown-drain delivery failure", async () => {
    const regs = createRegistrations();
    const { coordinator, log } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(
      createChannel({
        deliver: vi.fn(async () => {
          throw new Error("deliver boom");
        }),
      }),
    );

    // Enqueue during teardown — the shutting-down flag holds the items for the final
    // awaited drain rather than flushing them as an agent turn.
    regs.shutdownHooks.push({
      name: "emit",
      hook: () => {
        coordinator.deliver({ text: "queued", tier: "normal" });
      },
    });

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;

    expect(log.error).toHaveBeenCalledWith(expect.anything(), "shutdown delivery failed");
  });
});

describe("Coordinator.deliver during shutdown", () => {
  it("holds even an immediate delivery once shutting down", async () => {
    const { coordinator } = makeCoordinator(db, createAgent(createSession()));
    const delivered: Delivery[] = [];
    coordinator.attachChannel(
      createChannel({
        deliver: vi.fn(async (d: Delivery) => {
          delivered.push(d);
        }),
      }),
    );

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;

    // Now shutting down: an immediate delivery must not render straight to the channel.
    coordinator.deliver({ text: "late ack", immediate: true });
    expect(delivered).toEqual([]);
  });
});

describe("Coordinator.resumeSession bridging guards", () => {
  const writeFileSync = (path: string) =>
    import("node:fs").then(({ writeFileSync }) => writeFileSync(path, ""));

  it("injects no bridging context when the target was never closed before", async () => {
    const { coordinator, registry } = makeCoordinator(db, createAgent(createSession()));

    const targetFile = join(dir, "fresh.jsonl");
    await writeFileSync(targetFile);
    const target = registry.create("test", targetFile);

    await coordinator.resumeSession(target);

    let captured: unknown = "untouched";
    coordinator.hostFactory()({
      on: (_e: string, handler: () => unknown) => {
        captured = handler();
      },
    } as never);

    expect(captured).toBeUndefined();
  });

  it("injects no bridging context when nothing closed in the interim", async () => {
    const { coordinator, registry } = makeCoordinator(db, createAgent(createSession()));

    const targetFile = join(dir, "closed.jsonl");
    await writeFileSync(targetFile);
    const target = registry.create("test", targetFile);
    registry.update(target.id, { closedAt: new Date("2099-01-01T00:00:00.000Z") });
    const reloaded = registry.get(target.id);

    await coordinator.resumeSession(reloaded as NonNullable<typeof reloaded>);

    let captured: unknown = "untouched";
    coordinator.hostFactory()({
      on: (_e: string, handler: () => unknown) => {
        captured = handler();
      },
    } as never);

    expect(captured).toBeUndefined();
  });

  it("injects no bridging context when interim sessions have no summaries", async () => {
    const { coordinator, registry } = makeCoordinator(db, createAgent(createSession()));

    const targetFile = join(dir, "target2.jsonl");
    await writeFileSync(targetFile);
    const target = registry.create("test", targetFile);
    registry.update(target.id, { closedAt: new Date("2026-01-01T00:00:00.000Z") });

    const between = registry.create("test", join(dir, "between.jsonl"));
    registry.update(between.id, { closedAt: new Date("2026-01-01T01:00:00.000Z") });

    const reloaded = registry.get(target.id);
    await coordinator.resumeSession(reloaded as NonNullable<typeof reloaded>);

    let captured: unknown = "untouched";
    coordinator.hostFactory()({
      on: (_e: string, handler: () => unknown) => {
        captured = handler();
      },
    } as never);

    expect(captured).toBeUndefined();
  });
});

describe("Coordinator.drainQueueToChannel clears an armed timer", () => {
  it("delivers the held digest after disarming the pending re-check timer", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T12:00:00.000Z"));

    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    const delivered: Delivery[] = [];
    coordinator.attachChannel(
      createChannel({
        deliver: vi.fn(async (d: Delivery) => {
          delivered.push(d);
        }),
      }),
    );

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    // An undeliverable item arms the re-check timer; shutdown must clear it before draining.
    coordinator.deliver({ text: "deferred", tier: "normal" });

    controller.abort();
    await vi.runOnlyPendingTimersAsync();
    vi.useRealTimers();
    await loop;

    expect(delivered).toHaveLength(1);
    expect(delivered[0]?.text).toContain("deferred");
  });
});

describe("Coordinator.flushQueue with no channel attached", () => {
  it("submits a system-origin digest tagged with the system channel name", () => {
    const { coordinator } = makeCoordinator(db, createAgent(createSession()));
    const submitSpy = vi.spyOn(coordinator, "submit");

    coordinator.deliver({ text: "no channel", tier: "normal" });

    expect(submitSpy).toHaveBeenCalledTimes(1);
    expect(submitSpy.mock.calls[0]?.[0].channel).toBe("system");
  });
});

describe("Coordinator.hostFactory", () => {
  it("injects nothing when there is no pending context", () => {
    const { coordinator } = makeCoordinator(db, createAgent(createSession()));

    let captured: unknown = "untouched";
    const pi = {
      on: (_event: string, handler: () => unknown) => {
        captured = handler();
      },
    };
    coordinator.hostFactory()(pi as never);

    expect(captured).toBeUndefined();
  });
});

describe("Coordinator.closeActiveSessionIfIdle while busy", () => {
  it("skips the close when an exchange is in flight", async () => {
    let releasePrompt: (() => void) | null = null;
    const session = createSession({
      prompt: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            releasePrompt = resolve;
          }),
      ),
    });
    const { coordinator, log } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));

    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    expect(await coordinator.closeActiveSessionIfIdle()).toBe(false);
    expect(log.debug).toHaveBeenCalledWith("idle close skipped — exchange in flight");

    releasePrompt?.();
    controller.abort();
    await loop;
  });
});

describe("Coordinator.ensureSession with no persisted file", () => {
  it("records a null pi session file when the opened session has none", async () => {
    const session = createSession({ sessionFile: null });
    const { coordinator, registry } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));

    await vi.waitFor(() => expect(coordinator.current()).not.toBeNull());

    const id = coordinator.current()?.id as number;
    expect(registry.get(id)?.piSessionFile).toBeNull();

    controller.abort();
    await loop;
  });
});

describe("Coordinator.scheduleDelivery timer arming", () => {
  it("arms a re-check timer when the front item is not yet deliverable, then flushes on wake", async () => {
    vi.useFakeTimers();
    const base = new Date("2026-06-14T12:00:00.000Z");
    vi.setSystemTime(base);

    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    const submitSpy = vi.spyOn(coordinator, "submit");
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());
    submitSpy.mockClear();

    // lastExchangeAt is now ~base; a normal item's 120s idle window is unmet → timer armed.
    coordinator.deliver({ text: "deferred notice", tier: "normal" });
    expect(submitSpy).not.toHaveBeenCalled();

    // Advance past the idle window; the armed timer re-evaluates and flushes.
    await vi.advanceTimersByTimeAsync(121_000);
    expect(submitSpy).toHaveBeenCalledTimes(1);
    expect(submitSpy.mock.calls[0]?.[0].text).toContain("deferred notice");

    controller.abort();
    await vi.runOnlyPendingTimersAsync();
    vi.useRealTimers();
    await loop;
  });
});

describe("renderPrompt media rendering", () => {
  it("appends an attachments block when the message carries media", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(
      textMsg("look at this", {
        media: [
          { kind: "photo", path: "/tmp/a.png", description: "a cat" },
          { kind: "document", path: "/tmp/b.pdf" },
        ],
      }),
    );

    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    const prompt = session.prompt.mock.calls[0]?.[0] as string;
    expect(prompt).toContain("<attachments>");
    expect(prompt).toContain("photo at /tmp/a.png — a cat");
    expect(prompt).toContain("document at /tmp/b.pdf");

    controller.abort();
    await loop;
  });
});

describe("lastAssistantText extraction", () => {
  const lastTextOf = async (messages: unknown[]): Promise<string> => {
    const session = createSession({ messages });
    const regs = createRegistrations();
    let captured = "";
    regs.exchangeProcessors.push({
      name: "capture",
      process: async (ctx) => {
        captured = ctx.assistantText;
      },
    });

    const { coordinator } = makeCoordinator(db, createAgent(session), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());
    controller.abort();
    await loop;

    return captured;
  };

  it("returns the concatenated text of the last assistant message", async () => {
    expect(
      await lastTextOf([
        { role: "user", content: [{ type: "text", text: "ignored" }] },
        {
          role: "assistant",
          content: [
            { type: "text", text: "part one " },
            { type: "tool_use", id: "x" },
            { type: "text", text: "part two" },
          ],
        },
      ]),
    ).toBe("part one part two");
  });

  it("skips non-assistant and malformed-content messages", async () => {
    expect(
      await lastTextOf([
        { role: "assistant", content: [{ type: "text", text: "real answer" }] },
        { role: "assistant", content: "not an array" },
        null,
        { role: "tool", content: [{ type: "text", text: "tool noise" }] },
      ]),
    ).toBe("real answer");
  });

  it("returns an empty string when there is no assistant text at all", async () => {
    expect(
      await lastTextOf([{ role: "user", content: [{ type: "text", text: "only user" }] }]),
    ).toBe("");
  });
});

describe("Coordinator encoding-error quarantine", () => {
  it("marks the active session errored when an exchange fails with an encoding error", async () => {
    const session = createSession({
      prompt: vi.fn().mockRejectedValue(new Error("cannot encode surrogate pair")),
    });
    const { coordinator, registry, log } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    const sessionId = await vi.waitFor(() => {
      const id = coordinator.current()?.id;
      expect(id).toBeDefined();
      return id as number;
    });
    await vi.waitFor(() => expect(registry.get(sessionId)?.error).toBe(true));

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId }),
      "session quarantined — encoding failure",
    );

    controller.abort();
    await loop;
  });

  it("does not mark the session errored on a non-encoding (provider) error", async () => {
    const regs = createRegistrations();
    let exchangeDone = false;
    regs.exchangeProcessors.push({
      name: "signal",
      process: async () => {
        exchangeDone = true;
      },
    });

    const session = createSession({
      prompt: vi.fn().mockRejectedValue(new Error("429 too many requests")),
    });
    const { coordinator, registry } = makeCoordinator(db, createAgent(session), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    const sessionId = await vi.waitFor(() => {
      const id = coordinator.current()?.id;
      expect(id).toBeDefined();
      return id as number;
    });
    // The exchange processor runs only after the encoding-error check, so when it fires the
    // decision not to quarantine is already settled.
    await vi.waitFor(() => expect(exchangeDone).toBe(true));

    expect(registry.get(sessionId)?.error).toBe(false);

    controller.abort();
    await loop;
  });

  it("skips post-processing for a quarantined session while still closing it", async () => {
    const regs = createRegistrations();
    const processed: number[] = [];
    regs.postProcessors.push({
      name: "should-not-run",
      process: async (ctx) => {
        if (ctx.session != null) processed.push(ctx.session.id);
      },
    });

    const { coordinator, registry, log } = makeCoordinator(db, createAgent(createSession()), regs);

    const dangling = registry.create("test", "/tmp/active.jsonl");
    registry.markErrored(dangling.id);

    await coordinator.recoverUnprocessedSessions();

    expect(processed).toEqual([]);
    expect(registry.get(dangling.id)?.closedAt).not.toBeNull();
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: dangling.id }),
      "post-processing skipped — session quarantined",
    );
  });

  it("keeps handling messages after an exchange ends in an encoding error", async () => {
    // First exchange fails with an encoding error; the second must still be handled. The active
    // session is not force-closed on quarantine, so the loop carries on against the same session —
    // but its exchange processors are skipped, since a quarantined session's state is not maintained.
    let attempt = 0;
    const session = createSession({
      prompt: vi.fn(() => {
        attempt += 1;
        return attempt === 1
          ? Promise.reject(new Error("invalid utf-8 byte sequence"))
          : Promise.resolve(undefined);
      }),
    });
    const regs = createRegistrations();
    const processed: string[] = [];
    regs.exchangeProcessors.push({
      name: "capture",
      process: async (ctx) => {
        processed.push(ctx.userText);
      },
    });

    const { coordinator } = makeCoordinator(db, createAgent(session), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("first"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalledTimes(1));

    // The loop survives the quarantine and handles the next message against the same session.
    coordinator.submit(textMsg("second"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalledTimes(2));

    // The quarantined session's derived state is left untouched.
    expect(processed).toEqual([]);

    controller.abort();
    await loop;
  });
});

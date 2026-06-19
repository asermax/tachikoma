import { mkdtemp, writeFile } from "node:fs/promises";
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
  sessionFile: string | null;
  dispose: ReturnType<typeof vi.fn>;
  messages: unknown[];
  subscribe: ReturnType<typeof vi.fn>;
  prompt: ReturnType<typeof vi.fn>;
  steer: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
  systemPrompt: string;
  sessionManager: {
    getEntries: () => unknown[];
    getLeafId: () => string | null;
    getBranch: (fromId?: string) => unknown[];
  };
}

const createSession = (overrides: Partial<FakeSession> = {}): FakeSession => ({
  sessionFile: "/tmp/trunk.jsonl",
  dispose: vi.fn(),
  messages: [],
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
  const trunkState = new TrunkState(new KeyValueState(db, "trunk"));
  const events = new EventBus(log);
  const coordinator = new Coordinator(trunkState, agent, regs, events, log, "UTC", now);

  return { coordinator, trunkState, events, log };
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
  it("opens a trunk, runs open hooks once, responds, and runs exchange processors", async () => {
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
    coordinator.submit(textMsg("again"));
    await vi.waitFor(() => expect(processed).toHaveLength(2));

    // The trunk opens once for the day — its open hook fires exactly once across both turns.
    expect(openHook).toHaveBeenCalledTimes(1);
    expect(opened).toHaveBeenCalledTimes(1);
    expect(processed[0]).toEqual({ userText: "question" });

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

    controller.abort();
    await loop;
  });

  it("hands the inbound middleware the live trunk", async () => {
    const regs = createRegistrations();
    let sawTrunkFile: string | null | undefined;
    regs.inboundMiddleware.push(async (_m, context, next) => {
      sawTrunkFile = context.trunk?.sessionFile;
      await next();
    });

    const { coordinator } = makeCoordinator(db, createAgent(createSession()), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(sawTrunkFile).toBe("/tmp/trunk.jsonl"));

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

  it("reuses the already-open trunk instead of opening a new one", async () => {
    const session = createSession();
    const agent = createAgent(session);
    const { coordinator } = makeCoordinator(db, agent);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("one"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalledTimes(1));

    coordinator.submit(textMsg("two"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalledTimes(2));

    expect((agent.open as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(1);

    controller.abort();
    await loop;
  });
});

describe("Coordinator restart mid-day", () => {
  it("reopens from the active pointer and continues on the trunk", async () => {
    const file = join(dir, "trunk.jsonl");
    await import("node:fs/promises").then(({ writeFile }) => writeFile(file, ""));

    // Seed an active pointer for today (as a prior run would have left it).
    const trunkState = new TrunkState(new KeyValueState(db, "trunk"));
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "UTC" }).format(new Date());
    trunkState.promoteToActive({
      sessionFile: file,
      day: today,
      openedAt: new Date().toISOString(),
    });

    const branch = vi.fn();
    const session = createSession({
      sessionFile: file,
      sessionManager: {
        getEntries: () => [],
        getLeafId: () => null,
        getBranch: () => [],
        // biome-ignore lint/suspicious/noExplicitAny: re-seat probe
      } as any,
    });
    // biome-ignore lint/suspicious/noExplicitAny: attach a branch() spy for the re-seat path
    (session.sessionManager as any).branch = branch;

    const agent = createAgent(session);
    const { coordinator } = makeCoordinator(db, agent);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("continue please"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    // Reopened from the pointer (same-day) rather than creating a fresh trunk.
    expect((agent.open as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toEqual({
      sessionFile: file,
    });

    controller.abort();
    await loop;
  });
});

describe("Coordinator system-origin delivery placement", () => {
  it("appends a system-origin turn to the current branch without invoking the classifier", async () => {
    const regs = createRegistrations();
    const classified: InboundMessage[] = [];
    // A stand-in boundary middleware that "classifies" only non-skip messages.
    regs.inboundMiddleware.push(async (message, _context, next) => {
      if (message.metadata.boundary !== "skip") classified.push(message);
      await next();
    });

    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session), regs);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(
      textMsg("scheduled task fired", { metadata: { origin: "system", boundary: "skip" } }),
    );
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    // The turn streamed to the trunk, but the classifier middleware never saw it.
    expect(classified).toEqual([]);

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
});

describe("Coordinator.abortExchange", () => {
  it("aborts the active session's run", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));

    // No active trunk yet: should be a no-op.
    await coordinator.abortExchange();
    expect(session.abort).not.toHaveBeenCalled();

    coordinator.attachChannel(createChannel());
    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.activeTrunkSession()).not.toBeNull());

    await coordinator.abortExchange();
    expect(session.abort).toHaveBeenCalledTimes(1);

    controller.abort();
    await loop;
  });
});

describe("Coordinator.run shutdown sequence", () => {
  it("announces wrap-up and closes the trunk via shutdownStatus", async () => {
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
    await vi.waitFor(() => expect(coordinator.activeTrunkSession()).not.toBeNull());

    controller.abort();
    await loop;

    expect(shutdownStatus).toHaveBeenCalledWith("Wrapping up the conversation…");
    expect(shutdownStatus).toHaveBeenCalledWith("Done");
    expect(ppCalls).toContain("finalizer");
    expect(coordinator.activeTrunkSession()).toBeNull();
    expect(session.dispose).toHaveBeenCalled();
  });

  it("skips the wrap-up announcement when there is no active trunk", async () => {
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
});

describe("Coordinator.run post-processing on close", () => {
  it("runs processors by phase over the trunk context", async () => {
    const regs = createRegistrations();
    const calls: string[] = [];
    const transcriptPaths: (string | null)[] = [];

    regs.postProcessors.push({
      name: "memory",
      phase: "main",
      process: async (ctx) => {
        calls.push("memory");
        transcriptPaths.push(ctx.transcriptPath);
      },
    });
    regs.postProcessors.push({
      name: "archive",
      phase: "finalize",
      process: async () => {
        calls.push("archive");
      },
    });

    const { coordinator, events } = makeCoordinator(db, createAgent(createSession()), regs);
    const postProcessed = vi.fn();
    events.on("session:post-processed", postProcessed);
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    coordinator.submit(textMsg("hi"));
    await vi.waitFor(() => expect(coordinator.activeTrunkSession()).not.toBeNull());

    controller.abort();
    await loop;

    expect(calls.indexOf("memory")).toBeLessThan(calls.indexOf("archive"));
    expect(transcriptPaths[0]).toBe("/tmp/trunk.jsonl");
    expect(postProcessed).toHaveBeenCalledTimes(1);
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

describe("Coordinator new-day close surfaces pipeline progress to the channel", () => {
  it("shows the close lead-in then per-processor progress on the active channel, before the response", async () => {
    const staleFile = join(dir, "yesterday.jsonl");
    await writeFile(staleFile, "");

    // Stale session opens for yesterday's file; a fresh session opens for today's new trunk.
    const staleSession = createSession({ sessionFile: staleFile });
    const todaySession = createSession({
      sessionFile: "/tmp/today.jsonl",
      messages: [{ role: "assistant", content: [{ type: "text", text: "good morning" }] }],
    });
    const agent = {
      open: vi.fn(async (options?: { sessionFile?: string }) =>
        options?.sessionFile === staleFile ? staleSession : todaySession,
      ),
    } as unknown as AgentManager;

    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "memory-trunk-close",
      phase: "main",
      statusLabel: "Processing memories",
      process: async () => {},
    });
    regs.postProcessors.push({
      name: "transcript-archive",
      phase: "finalize",
      statusLabel: "Archiving transcript",
      process: async () => {},
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, agent, regs, now);

    // Active pointer left on the previous day → the first message of the new day closes it.
    trunkState.promoteToActive({
      sessionFile: staleFile,
      day: "2026-06-14",
      openedAt: "2026-06-14T10:00:00Z",
    });

    const status = vi.fn();
    const respond = vi.fn(drainExchange);
    coordinator.attachChannel(createChannel({ status, respond }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("good morning"));

    // The close runs and surfaces its progress before the response streams.
    await vi.waitFor(() => expect(respond).toHaveBeenCalled());

    expect(status).toHaveBeenNthCalledWith(1, "Closing yesterday's trunk…");
    expect(status).toHaveBeenNthCalledWith(2, "Processing memories…");
    expect(status).toHaveBeenNthCalledWith(3, "Archiving transcript…");

    controller.abort();
    await loop;
  });

  it("degrades gracefully when the channel has no status() — no throw, response still proceeds", async () => {
    const staleFile = join(dir, "yesterday.jsonl");
    await writeFile(staleFile, "");

    const staleSession = createSession({ sessionFile: staleFile });
    const todaySession = createSession();
    const agent = {
      open: vi.fn(async (options?: { sessionFile?: string }) =>
        options?.sessionFile === staleFile ? staleSession : todaySession,
      ),
    } as unknown as AgentManager;

    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "memory-trunk-close",
      phase: "main",
      statusLabel: "Processing memories",
      process: async () => {},
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, agent, regs, now);
    trunkState.promoteToActive({
      sessionFile: staleFile,
      day: "2026-06-14",
      openedAt: "2026-06-14T10:00:00Z",
    });

    // createChannel() with no status override → channel.status is undefined.
    const respond = vi.fn(drainExchange);
    coordinator.attachChannel(createChannel({ respond }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hi"));

    // No throw despite no status(); the response still proceeds.
    await vi.waitFor(() => expect(respond).toHaveBeenCalled());

    controller.abort();
    await loop;
  });
});

describe("Coordinator.closeTrunkIfDue (nightly close trigger)", () => {
  it("closes the trunk when one is active and idle", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    // Open the trunk via a normal exchange, then let the loop go idle.
    coordinator.submit(textMsg("hello"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    await coordinator.closeTrunkIfDue();

    expect(session.dispose).toHaveBeenCalledTimes(1);
    // A second trigger is a no-op — the trunk is already closed.
    await coordinator.closeTrunkIfDue();
    expect(session.dispose).toHaveBeenCalledTimes(1);

    controller.abort();
    await loop;
  });

  it("is a no-op when no trunk has been opened", async () => {
    const session = createSession();
    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(createChannel());

    await coordinator.closeTrunkIfDue();

    expect(session.dispose).not.toHaveBeenCalled();
  });

  it("skips the close while an exchange is in flight", async () => {
    const session = createSession();

    let release: (() => void) | null = null;
    const channel = createChannel({
      respond: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            release = resolve;
          }),
      ),
    });

    const { coordinator } = makeCoordinator(db, createAgent(session));
    coordinator.attachChannel(channel);

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    coordinator.submit(textMsg("hello"));
    // Wait until the exchange is streaming (respond entered, not yet resolved).
    await vi.waitFor(() => expect(channel.respond).toHaveBeenCalled());

    await coordinator.closeTrunkIfDue();
    expect(session.dispose).not.toHaveBeenCalled();

    release?.();
    controller.abort();
    await loop;
  });

  it("stays silent during the idle close (no response follows to reclaim a lead-in)", async () => {
    const session = createSession();
    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "memory-trunk-close",
      phase: "main",
      statusLabel: "Processing memories",
      process: async () => {},
    });

    const { coordinator } = makeCoordinator(db, createAgent(session), regs);
    const status = vi.fn();
    coordinator.attachChannel(createChannel({ status }));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    // Open the trunk via a normal exchange, then let the loop go idle.
    coordinator.submit(textMsg("hello"));
    await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());

    status.mockClear();

    await coordinator.closeTrunkIfDue();

    // Idle close surfaces nothing — nothing follows to reclaim a status lead-in.
    expect(status).not.toHaveBeenCalled();
    expect(session.dispose).toHaveBeenCalledTimes(1);

    controller.abort();
    await loop;
  });
});

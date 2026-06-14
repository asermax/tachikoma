import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import type { Channel, Delivery } from "../src/channels/types.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { EventBus } from "../src/events.ts";
import { createRegistrations } from "../src/extensions/registrations.ts";
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

const createChannel = (delivered: Delivery[]): Channel => ({
  name: "test",
  start: vi.fn(),
  respond: vi.fn(),
  deliver: vi.fn(async (delivery: Delivery) => {
    delivered.push(delivery);
  }),
  stop: vi.fn(),
});

const noopAgent = { open: vi.fn() } as unknown as AgentManager;

// A session mock just complete enough for streamPrompt (subscribe + prompt) so a wired
// loop can process an injected turn without a real pi session.
const createRunnableAgent = () =>
  ({
    open: vi.fn().mockResolvedValue({
      sessionFile: "/tmp/active.jsonl",
      dispose: vi.fn(),
      messages: [],
      subscribe: () => () => {},
      prompt: vi.fn().mockResolvedValue(undefined),
    }),
  }) as unknown as AgentManager;

let db: AppDatabase;
let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-coordinator-delivery-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Coordinator background delivery", () => {
  it("renders an immediate command ack straight to the channel, bypassing the queue", () => {
    const log = createFakeLog();
    const coordinator = new Coordinator(
      new SessionRegistry(db),
      noopAgent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    const delivered: Delivery[] = [];
    coordinator.attachChannel(createChannel(delivered));
    const submitSpy = vi.spyOn(coordinator, "submit");

    coordinator.deliver({ text: "🆕 Started a fresh session.", immediate: true });

    expect(delivered).toEqual([{ text: "🆕 Started a fresh session.", immediate: true }]);
    expect(submitSpy).not.toHaveBeenCalled();
  });

  it("delivers an idle-deliverable notice as one system-origin turn, never a channel render", () => {
    const log = createFakeLog();
    const coordinator = new Coordinator(
      new SessionRegistry(db),
      noopAgent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    const delivered: Delivery[] = [];
    coordinator.attachChannel(createChannel(delivered));
    const submitSpy = vi.spyOn(coordinator, "submit");

    // No prior exchange → inherently idle → the queue drains immediately as a turn.
    coordinator.deliver({ text: "task done", tier: "normal" });

    expect(submitSpy).toHaveBeenCalledTimes(1);
    const message = submitSpy.mock.calls[0]?.[0];
    expect(message?.metadata).toMatchObject({ origin: "system", boundary: "skip" });
    expect(message?.text).toContain("task done");
    // System-origin injection, not a direct channel render.
    expect(delivered).toEqual([]);
  });

  it("wakes the parked run loop to inject a queued notice as a turn", async () => {
    const log = createFakeLog();
    const coordinator = new Coordinator(
      new SessionRegistry(db),
      createRunnableAgent(),
      createRegistrations(),
      new EventBus(log),
      log,
    );
    coordinator.attachChannel(createChannel([]));
    const submitSpy = vi.spyOn(coordinator, "submit");

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    // Let the loop reach its parked state (empty inbox) before the notice arrives.
    await Promise.resolve();
    expect(submitSpy).not.toHaveBeenCalled();

    coordinator.deliver({ text: "background ping", tier: "normal" });

    // The enqueue flushes (no prior exchange → inherently idle) and submit wakes the loop,
    // which consumes the injected digest turn rather than re-parking with it stranded.
    await vi.waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy.mock.calls[0]?.[0].text).toContain("background ping");

    controller.abort();
    await loop;
  });

  it("drains items queued during shutdown to the channel as one tier-ordered digest", async () => {
    const log = createFakeLog();
    const regs = createRegistrations();
    const coordinator = new Coordinator(
      new SessionRegistry(db),
      noopAgent,
      regs,
      new EventBus(log),
      log,
    );

    const delivered: Delivery[] = [];
    coordinator.attachChannel(createChannel(delivered));
    const submitSpy = vi.spyOn(coordinator, "submit");

    // Mirrors the notifications router's onShutdown hook: it pushes pending notices into
    // the queue during teardown, where the shutting-down flag holds them for the final
    // awaited drain instead of an agent turn.
    regs.shutdownHooks.push({
      name: "emit",
      hook: () => {
        coordinator.deliver({ text: "info notice", tier: "low" });
        coordinator.deliver({ text: "urgent notice", tier: "urgent" });
        coordinator.deliver({ text: "warning notice", tier: "normal" });
      },
    });

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;

    // One digest to the channel, never re-submitted to the dead inbox.
    expect(submitSpy).not.toHaveBeenCalled();
    expect(delivered).toHaveLength(1);

    const digest = delivered[0]?.text ?? "";
    expect(digest.indexOf("urgent notice")).toBeLessThan(digest.indexOf("warning notice"));
    expect(digest.indexOf("warning notice")).toBeLessThan(digest.indexOf("info notice"));
  });
});

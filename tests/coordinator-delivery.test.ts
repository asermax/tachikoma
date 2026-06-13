import { mkdtemp, writeFile } from "node:fs/promises";
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

describe("Coordinator delivery priority ordering", () => {
  it("flushes held deliveries highest-priority first, stable within a priority", async () => {
    const log = createFakeLog();
    const registry = new SessionRegistry(db);

    const sessionFile = join(dir, "session.jsonl");
    await writeFile(sessionFile, "");
    const record = registry.create("test", sessionFile);

    const agent = {
      open: vi.fn().mockResolvedValue({
        sessionFile,
        dispose: vi.fn(),
      }),
    } as unknown as AgentManager;

    const coordinator = new Coordinator(
      registry,
      agent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    const delivered: string[] = [];
    const channel: Channel = {
      name: "test",
      start: vi.fn(),
      respond: vi.fn(),
      deliver: vi.fn(async (delivery: Delivery) => {
        delivered.push(delivery.text);
      }),
      stop: vi.fn(),
    };
    coordinator.attachChannel(channel);

    // An active session means idle-gated deliveries are held, not sent immediately.
    await coordinator.resumeSession(record);

    coordinator.deliver({ text: "info", gate: "idle", priority: 1 });
    coordinator.deliver({ text: "warning", gate: "idle", priority: 2 });
    coordinator.deliver({ text: "urgent", gate: "idle", priority: 3 });
    coordinator.deliver({ text: "warning-2", gate: "idle", priority: 2 });

    expect(delivered).toEqual([]);

    // run()'s shutdown finally force-flushes held deliveries (and closes the session).
    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;
    // sendDelivery is fire-and-forget; let its channel.deliver promises settle.
    await vi.waitFor(() => expect(delivered).toHaveLength(4));

    expect(delivered).toEqual(["urgent", "warning", "warning-2", "info"]);
  });

  it("awaits the channel delivery of held notices before the run loop resolves", async () => {
    const log = createFakeLog();
    const registry = new SessionRegistry(db);

    const sessionFile = join(dir, "session.jsonl");
    await writeFile(sessionFile, "");
    const record = registry.create("test", sessionFile);

    const agent = {
      open: vi.fn().mockResolvedValue({ sessionFile, dispose: vi.fn() }),
    } as unknown as AgentManager;

    const coordinator = new Coordinator(
      registry,
      agent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    let releaseDeliver: (() => void) | null = null;
    const deliverGate = new Promise<void>((resolve) => {
      releaseDeliver = resolve;
    });

    const order: string[] = [];
    const channel: Channel = {
      name: "test",
      start: vi.fn(),
      respond: vi.fn(),
      deliver: vi.fn(async () => {
        await deliverGate;
        order.push("delivered");
      }),
      stop: vi.fn(),
    };
    coordinator.attachChannel(channel);

    await coordinator.resumeSession(record);
    coordinator.deliver({ text: "held", gate: "idle", priority: 1 });

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal).then(() => order.push("loop-done"));
    controller.abort();

    // The loop must not resolve while channel.deliver is still pending.
    await Promise.resolve();
    expect(order).toEqual([]);

    (releaseDeliver as unknown as () => void)();
    await loop;

    expect(order).toEqual(["delivered", "loop-done"]);
  });

  it("delivers a held agent-target notice to the channel at shutdown instead of the dead inbox", async () => {
    const log = createFakeLog();
    const registry = new SessionRegistry(db);

    const sessionFile = join(dir, "session.jsonl");
    await writeFile(sessionFile, "");
    const record = registry.create("test", sessionFile);

    const agent = {
      open: vi.fn().mockResolvedValue({ sessionFile, dispose: vi.fn() }),
    } as unknown as AgentManager;

    const coordinator = new Coordinator(
      registry,
      agent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    const delivered: string[] = [];
    const channel: Channel = {
      name: "test",
      start: vi.fn(),
      respond: vi.fn(),
      deliver: vi.fn(async (delivery: Delivery) => {
        delivered.push(delivery.text);
      }),
      stop: vi.fn(),
    };
    coordinator.attachChannel(channel);

    await coordinator.resumeSession(record);
    coordinator.deliver({ text: "agent notice", gate: "idle", target: "agent" });

    const submitSpy = vi.spyOn(coordinator, "submit");

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);
    controller.abort();
    await loop;

    // The agent-target branch must be skipped during shutdown — no re-submit to the
    // dead inbox; the text falls through to the channel so it is still surfaced.
    expect(submitSpy).not.toHaveBeenCalled();
    expect(delivered).toEqual(["agent notice"]);
  });
});

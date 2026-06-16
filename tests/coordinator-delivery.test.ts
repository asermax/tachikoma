import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import type { Channel, Delivery, Exchange } from "../src/channels/types.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";
import { EventBus } from "../src/events.ts";
import { createRegistrations, type Registrations } from "../src/extensions/registrations.ts";
import type { Logger } from "../src/log.ts";
import { TrunkState } from "../src/sessions/trunk.ts";

const createFakeLog = () => {
  const log = { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

const createChannel = (delivered: Delivery[]): Channel => ({
  name: "test",
  start: vi.fn(),
  respond: vi.fn(async (exchange: Exchange) => {
    for await (const _ of exchange.events) {
      // drain
    }
  }),
  deliver: vi.fn(async (delivery: Delivery) => {
    delivered.push(delivery);
  }),
  stop: vi.fn(),
});

const fakeSession = () => ({
  sessionFile: "/tmp/trunk.jsonl",
  dispose: vi.fn(),
  messages: [],
  subscribe: () => () => {},
  prompt: vi.fn().mockResolvedValue(undefined),
  sessionManager: { getEntries: () => [], getLeafId: () => null, getBranch: () => [] },
});

const noopAgent = { open: vi.fn() } as unknown as AgentManager;

const createRunnableAgent = () =>
  ({
    open: vi.fn().mockResolvedValue(fakeSession()),
  }) as unknown as AgentManager;

const makeCoordinator = (
  db: AppDatabase,
  agent: AgentManager,
  regs: Registrations = createRegistrations(),
  now: () => Date = () => new Date(),
) => {
  const log = createFakeLog();
  const trunkState = new TrunkState(new KeyValueState(db, "trunk"));
  const coordinator = new Coordinator(trunkState, agent, regs, new EventBus(log), log, "UTC", now);

  return { coordinator, trunkState, log };
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

describe("Coordinator background delivery", () => {
  it("renders an immediate command ack straight to the channel, bypassing the queue", () => {
    const { coordinator } = makeCoordinator(db, noopAgent);

    const delivered: Delivery[] = [];
    coordinator.attachChannel(createChannel(delivered));
    const submitSpy = vi.spyOn(coordinator, "submit");

    coordinator.deliver({ text: "🆕 Started a fresh topic.", immediate: true });

    expect(delivered).toEqual([{ text: "🆕 Started a fresh topic.", immediate: true }]);
    expect(submitSpy).not.toHaveBeenCalled();
  });

  it("holds a delivery while no trunk is live, then drains once a trunk opens", async () => {
    const { coordinator } = makeCoordinator(db, createRunnableAgent());
    coordinator.attachChannel(createChannel([]));
    const submitSpy = vi.spyOn(coordinator, "submit");

    // No run loop started → no trunk live yet. The delivery must be held, not flushed.
    coordinator.deliver({ text: "task done", tier: "normal" });
    expect(submitSpy).not.toHaveBeenCalled();

    // Start the loop: the first delivery is itself the first event of the day → opens a trunk and
    // drains as one system-origin turn.
    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    await vi.waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    const message = submitSpy.mock.calls[0]?.[0];
    expect(message?.metadata).toMatchObject({ origin: "system", boundary: "skip" });
    expect(message?.text).toContain("task done");

    controller.abort();
    await loop;
  });

  it("opens a new trunk for a background delivery arriving after a close", async () => {
    const agent = createRunnableAgent();
    const { coordinator } = makeCoordinator(db, agent);
    coordinator.attachChannel(createChannel([]));

    const controller = new AbortController();
    const loop = coordinator.run(controller.signal);

    // First delivery opens the trunk and drains.
    coordinator.deliver({ text: "first", tier: "normal" });
    await vi.waitFor(() =>
      expect((agent.open as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(1),
    );

    controller.abort();
    await loop;

    // After shutdown the trunk is closed; activeTrunkSession is null.
    expect(coordinator.activeTrunkSession()).toBeNull();
  });

  it("drains items queued during shutdown to the channel as one tier-ordered digest", async () => {
    const regs = createRegistrations();
    const { coordinator } = makeCoordinator(db, noopAgent, regs);

    const delivered: Delivery[] = [];
    coordinator.attachChannel(createChannel(delivered));
    const submitSpy = vi.spyOn(coordinator, "submit");

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

    expect(submitSpy).not.toHaveBeenCalled();
    expect(delivered).toHaveLength(1);

    const digest = delivered[0]?.text ?? "";
    expect(digest.indexOf("urgent notice")).toBeLessThan(digest.indexOf("warning notice"));
    expect(digest.indexOf("warning notice")).toBeLessThan(digest.indexOf("info notice"));
  });
});

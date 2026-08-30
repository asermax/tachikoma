import { describe, expect, it, vi } from "vitest";

import {
  DISPATCH_BACKGROUND_TASK_EVENT,
  EventBus,
  NOTIFY_EVENT,
  SESSION_TOPIC_CHANGED_EVENT,
  SEVERITIES,
} from "../src/events.ts";
import {
  NOTIFY_EVENT as REEXPORTED_NOTIFY_EVENT,
  SEVERITIES as REEXPORTED_SEVERITIES,
} from "../src/extensions/notifications/payload.ts";
import type { Logger } from "../src/log.ts";

const fakeLog = { error: vi.fn() } as unknown as Logger;

const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

describe("EventBus", () => {
  it("delivers payloads to subscribers", async () => {
    const bus = new EventBus(fakeLog);
    const seen: unknown[] = [];

    bus.on("ping", (payload) => {
      seen.push(payload);
    });
    bus.emit("ping", { n: 1 });
    await flush();

    expect(seen).toEqual([{ n: 1 }]);
  });

  it("isolates failing handlers from the rest", async () => {
    const bus = new EventBus(fakeLog);
    const seen: unknown[] = [];

    bus.on("ping", () => {
      throw new Error("boom");
    });
    bus.on("ping", (payload) => {
      seen.push(payload);
    });
    bus.emit("ping", 42);
    await flush();

    expect(seen).toEqual([42]);
  });

  it("supports unsubscribe", async () => {
    const bus = new EventBus(fakeLog);
    const handler = vi.fn();

    const off = bus.on("ping", handler);
    off();
    bus.emit("ping", null);
    await flush();

    expect(handler).not.toHaveBeenCalled();
  });
});

describe("event contracts", () => {
  it("keeps the neutral constants' values stable", () => {
    expect(SESSION_TOPIC_CHANGED_EVENT).toBe("session:topic-changed");
    expect(NOTIFY_EVENT).toBe("notify");
    expect(SEVERITIES).toEqual({ info: "info", warning: "warning", urgent: "urgent" });
  });

  it("namespaces the background-task dispatch event under task:", () => {
    expect(DISPATCH_BACKGROUND_TASK_EVENT).toBe("task:dispatch-background");
  });

  it("delivers a dispatch payload through the bus", async () => {
    const bus = new EventBus(fakeLog);
    const seen: unknown[] = [];

    bus.on(DISPATCH_BACKGROUND_TASK_EVENT, (payload) => {
      seen.push(payload);
    });
    bus.emit(DISPATCH_BACKGROUND_TASK_EVENT, {
      prompt: "Open PRs for the proposed skill changes",
      goal: "Land the proposals for review",
      source: "skill-evolution",
    });
    await flush();

    expect(seen).toEqual([
      {
        prompt: "Open PRs for the proposed skill changes",
        goal: "Land the proposals for review",
        source: "skill-evolution",
      },
    ]);
  });

  it("re-exports the notify contract from notifications/payload.ts unchanged", () => {
    expect(REEXPORTED_NOTIFY_EVENT).toBe(NOTIFY_EVENT);
    expect(REEXPORTED_SEVERITIES).toBe(SEVERITIES);
  });
});

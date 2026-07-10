import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventBus } from "../../src/events.ts";
import { NOTIFY_EVENT, parseNotifyPayload } from "../../src/extensions/notifications/payload.ts";
import { NotificationRouter, SEVERITY_TIER } from "../../src/extensions/notifications/router.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

const fixedNow = new Date("2026-06-12T10:00:00Z");

const FLUSH_WINDOW_SECONDS = 30;
const DEDUP_TTL_SECONDS = 60;

const createSetup = (now: () => Date = () => fixedNow) => {
  const deliver = vi.fn();
  const bus = new EventBus(fakeLog);
  const router = new NotificationRouter({
    deliver,
    flushWindowSeconds: FLUSH_WINDOW_SECONDS,
    dedupTtlSeconds: DEDUP_TTL_SECONDS,
    timezone: "UTC",
    log: fakeLog,
    now,
  });

  bus.on(NOTIFY_EVENT, (payload) => router.handle(payload));

  return { deliver, bus, router };
};

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("NotificationRouter", () => {
  it("delivers urgent notifications immediately at the urgent tier with the source prefix", async () => {
    const { deliver, bus } = createSetup();

    bus.emit(NOTIFY_EVENT, { text: "server down", severity: "urgent", source: "monitor" });
    await vi.advanceTimersByTimeAsync(0);

    expect(deliver).toHaveBeenCalledExactlyOnceWith({
      text: "--- Notification ---\nSource: monitor\nTime: 2026-06-12 10:00 UTC\n\nserver down",
      tier: "urgent",
    });
  });

  it("holds a non-urgent notification for the flush window, then delivers at its tier", async () => {
    const { deliver, bus } = createSetup();

    bus.emit(NOTIFY_EVENT, {
      title: "Build finished",
      text: "all green",
      severity: "info",
      source: "ci",
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(deliver).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(FLUSH_WINDOW_SECONDS * 1000);

    expect(deliver).toHaveBeenCalledExactlyOnceWith({
      text: "--- Notification ---\nSource: ci\nTime: 2026-06-12 10:00 UTC\n\nBuild finished\n\nall green",
      tier: "low",
    });
  });

  it("combines several accumulated non-urgent notices into one digest delivery", async () => {
    const { deliver, bus } = createSetup();

    bus.emit(NOTIFY_EVENT, { text: "build finished", severity: "info", source: "ci" });
    bus.emit(NOTIFY_EVENT, { text: "disk almost full", severity: "warning", source: "monitor" });
    await vi.advanceTimersByTimeAsync(FLUSH_WINDOW_SECONDS * 1000);

    expect(deliver).toHaveBeenCalledTimes(1);

    const { text, tier } = deliver.mock.calls[0]?.[0] ?? {};
    // The digest inherits the highest tier among its items (warning → normal here).
    expect(tier).toBe(SEVERITY_TIER.warning);
    expect(text).toContain("--- Notifications digest ---");
    expect(text).toContain("— Item 1 (info, source: ci) —\nbuild finished");
    expect(text).toContain("— Item 2 (warning, source: monitor) —\ndisk almost full");
  });

  it("lets urgent notifications bypass notices already pending in the window", async () => {
    const { deliver, bus } = createSetup();

    bus.emit(NOTIFY_EVENT, { text: "minor note", severity: "info", source: "ci" });
    bus.emit(NOTIFY_EVENT, { text: "act now", severity: "urgent", source: "monitor" });
    await vi.advanceTimersByTimeAsync(0);

    expect(deliver).toHaveBeenCalledTimes(1);
    expect(deliver.mock.calls[0]?.[0]).toMatchObject({ tier: "urgent" });

    await vi.advanceTimersByTimeAsync(FLUSH_WINDOW_SECONDS * 1000);

    expect(deliver).toHaveBeenCalledTimes(2);
    expect(deliver.mock.calls[1]?.[0]).toMatchObject({ tier: "low" });
    expect(deliver.mock.calls[1]?.[0].text).toContain("minor note");
  });

  it("skips payloads without notification text", async () => {
    const { deliver, bus } = createSetup();

    bus.emit(NOTIFY_EVENT, { source: "tasks", status: "failed", message: "boom" });
    bus.emit(NOTIFY_EVENT, "not an object");
    await vi.advanceTimersByTimeAsync(FLUSH_WINDOW_SECONDS * 2 * 1000);

    expect(deliver).not.toHaveBeenCalled();
  });

  it("flush() is a no-op when nothing is pending", () => {
    const { deliver, router } = createSetup();

    router.flush();

    expect(deliver).not.toHaveBeenCalled();
  });

  it("flushNow() clears the pending timer and emits a single pending notice immediately", () => {
    const { deliver, router } = createSetup();

    router.handle({ text: "build finished", severity: "info", source: "ci" });

    router.flushNow();

    expect(deliver).toHaveBeenCalledTimes(1);
    expect(deliver.mock.calls[0]?.[0]).toMatchObject({ tier: "low" });
    expect(deliver.mock.calls[0]?.[0].text).toContain("build finished");

    // The window timer was cleared by flushNow — letting it elapse must not re-deliver.
    vi.advanceTimersByTime(FLUSH_WINDOW_SECONDS * 1000);

    expect(deliver).toHaveBeenCalledTimes(1);
  });

  it("flushNow() emits a digest when several notices are pending", () => {
    const { deliver, router } = createSetup();

    router.handle({ text: "build finished", severity: "info", source: "ci" });
    router.handle({ text: "disk almost full", severity: "warning", source: "monitor" });

    router.flushNow();

    expect(deliver).toHaveBeenCalledTimes(1);
    expect(deliver.mock.calls[0]?.[0].text).toContain("--- Notifications digest ---");
  });
});

describe("NotificationRouter dedup TTL guard", () => {
  // Drives router.handle() directly with a mutable clock — the EventBus dispatches
  // handlers as microtasks, which would collapse all emits to one observed instant.
  const setupWithClock = () => {
    let clock = new Date("2026-06-12T10:00:00Z").getTime();
    const { deliver, router } = createSetup(() => new Date(clock));

    return {
      deliver,
      router,
      advanceSeconds: (seconds: number) => {
        clock += seconds * 1000;
      },
    };
  };

  const urgent = { text: "server down", severity: "urgent", source: "monitor" } as const;

  it("suppresses an identical (source + text) notice within the dedup window", () => {
    const { deliver, router, advanceSeconds } = setupWithClock();

    router.handle(urgent);
    advanceSeconds(30);
    router.handle(urgent);

    expect(deliver).toHaveBeenCalledTimes(1);
  });

  it("lets an identical notice through once the dedup window has elapsed", () => {
    const { deliver, router, advanceSeconds } = setupWithClock();

    router.handle(urgent);
    advanceSeconds(DEDUP_TTL_SECONDS + 1);
    router.handle(urgent);

    expect(deliver).toHaveBeenCalledTimes(2);
  });

  it("does not suppress notices with different content from the same source", () => {
    const { deliver, router, advanceSeconds } = setupWithClock();

    router.handle(urgent);
    advanceSeconds(5);
    router.handle({ text: "server back up", severity: "urgent", source: "monitor" });

    expect(deliver).toHaveBeenCalledTimes(2);
  });
});

describe("parseNotifyPayload", () => {
  it("passes through a complete payload", () => {
    expect(
      parseNotifyPayload({ title: "Hi", text: "there", severity: "warning", source: "ci" }),
    ).toEqual({ title: "Hi", text: "there", severity: "warning", source: "ci" });
  });

  it("defaults missing or unknown severity and source", () => {
    expect(parseNotifyPayload({ text: "hello", severity: "error" })).toEqual({
      title: undefined,
      text: "hello",
      severity: "info",
      source: "unknown",
    });
  });

  it("rejects payloads without usable text", () => {
    expect(parseNotifyPayload(null)).toBeNull();
    expect(parseNotifyPayload({ message: "wrong field" })).toBeNull();
    expect(parseNotifyPayload({ text: "   " })).toBeNull();
  });
});

import { describe, expect, it, vi } from "vitest";

import { EventBus } from "../src/events.ts";
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

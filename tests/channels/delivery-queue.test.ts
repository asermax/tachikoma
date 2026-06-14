import { describe, expect, it } from "vitest";

import {
  compareQueued,
  evaluate,
  type QueuedItem,
  TIER_TIMING,
} from "../../src/channels/delivery-queue.ts";

const item = (tier: QueuedItem["tier"], enqueuedAt: number, text = tier): QueuedItem => ({
  text,
  tier,
  enqueuedAt,
});

describe("delivery-queue compareQueued", () => {
  it("orders by tier (urgent → normal → low) then FIFO by arrival", () => {
    const items = [
      item("low", 1, "low-1"),
      item("urgent", 5, "urgent-late"),
      item("normal", 2, "normal-1"),
      item("normal", 1, "normal-0"),
      item("urgent", 1, "urgent-early"),
    ];

    expect([...items].sort(compareQueued).map((i) => i.text)).toEqual([
      "urgent-early",
      "urgent-late",
      "normal-0",
      "normal-1",
      "low-1",
    ]);
  });
});

describe("delivery-queue evaluate", () => {
  const NOW = 1_000_000;

  it("returns null for an empty queue", () => {
    expect(evaluate(NOW, NOW, [])).toBeNull();
  });

  it("treats a null lastExchangeAt as inherently idle — deliverable now", () => {
    const result = evaluate(NOW, null, [item("low", NOW)]);

    expect(result).toEqual({ drain: [item("low", NOW)] });
  });

  it("holds the front item until its idle window since the last exchange elapses", () => {
    const lastExchangeAt = NOW;
    const items = [item("normal", NOW)];

    // Just had an exchange — Normal waits 120s.
    expect(evaluate(NOW, lastExchangeAt, items)).toEqual({
      wakeAt: lastExchangeAt + TIER_TIMING.normal.idleWindowMs,
    });

    // Idle window elapsed → deliverable.
    const later = lastExchangeAt + TIER_TIMING.normal.idleWindowMs;
    expect(evaluate(later, lastExchangeAt, items)).toEqual({ drain: items });
  });

  it("force-delivers a Normal item past its max-hold even while the user stays active", () => {
    const enqueuedAt = NOW;
    const items = [item("normal", enqueuedAt)];

    // lastExchangeAt keeps moving forward (active user) so the idle window never elapses,
    // but max-hold (900s from enqueue) eventually forces delivery.
    const past = enqueuedAt + TIER_TIMING.normal.maxHoldMs;
    const stillActive = past; // last exchange right now → idle window not elapsed

    expect(evaluate(past, stillActive, items)).toEqual({ drain: items });
  });

  it("never force-delivers a Low item — it only waits out its idle window", () => {
    const enqueuedAt = NOW;
    const items = [item("low", enqueuedAt)];

    // Far past any max-hold, but the user is active (recent exchange) → still held.
    const farFuture = enqueuedAt + 10 * TIER_TIMING.normal.maxHoldMs;
    expect(evaluate(farFuture, farFuture, items)).toEqual({
      wakeAt: farFuture + TIER_TIMING.low.idleWindowMs,
    });
  });

  it("lets the front item's timing govern the whole batch and drains all of it", () => {
    const lastExchangeAt = NOW;
    const items = [item("low", NOW, "low"), item("urgent", NOW, "urgent")];

    // Front is Urgent (30s window). Before it elapses → held.
    expect(evaluate(NOW, lastExchangeAt, items)).toEqual({
      wakeAt: lastExchangeAt + TIER_TIMING.urgent.idleWindowMs,
    });

    // Once the Urgent window elapses the entire batch (incl. the Low item) drains.
    const ready = lastExchangeAt + TIER_TIMING.urgent.idleWindowMs;
    const result = evaluate(ready, lastExchangeAt, items);
    expect(result).not.toBeNull();
    expect(result && "drain" in result && result.drain.map((i) => i.text)).toEqual([
      "urgent",
      "low",
    ]);
  });
});

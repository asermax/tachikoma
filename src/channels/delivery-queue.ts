import type { Delivery, DeliveryTier } from "./types.ts";

interface TierTiming {
  /** Lower sorts to the front of the queue. */
  order: number;
  /** Time since the last exchange the front item waits out before delivering. */
  idleWindowMs: number;
  /** Time since enqueue after which the item is force-delivered at the next idle; null = never. */
  maxHoldMs: number | null;
}

export const TIER_TIMING = {
  urgent: { order: 0, idleWindowMs: 30_000, maxHoldMs: 120_000 },
  normal: { order: 1, idleWindowMs: 120_000, maxHoldMs: 900_000 },
  low: { order: 2, idleWindowMs: 300_000, maxHoldMs: null },
} as const satisfies Record<DeliveryTier, TierTiming>;

export interface QueuedItem extends Delivery {
  /** Resolved tier (the optional `Delivery.tier` defaulted to "normal" at enqueue). */
  tier: DeliveryTier;
  /** Epoch ms when the item was enqueued; anchors its max-hold and breaks ties FIFO. */
  enqueuedAt: number;
}

/** Order by tier (Urgent → Normal → Low), then FIFO by arrival. */
export const compareQueued = (a: QueuedItem, b: QueuedItem): number =>
  TIER_TIMING[a.tier].order - TIER_TIMING[b.tier].order || a.enqueuedAt - b.enqueuedAt;

export type Evaluation = { drain: QueuedItem[] } | { wakeAt: number } | null;

/**
 * Decide whether the queue is deliverable now. The front item (highest tier, earliest
 * arrival) governs the whole batch: it is deliverable once the idle window since the last
 * exchange has elapsed, or its max-hold has expired. A null `lastExchangeAt` (no exchange
 * yet) counts as inherently idle. Returns the full sorted batch to drain, the next
 * actionable time to re-check, or null when the queue is empty.
 */
export const evaluate = (
  now: number,
  lastExchangeAt: number | null,
  items: QueuedItem[],
): Evaluation => {
  if (items.length === 0) return null;

  const sorted = [...items].sort(compareQueued);
  const front = sorted[0];
  if (front == null) return null;

  const timing = TIER_TIMING[front.tier];

  const idleReadyAt = (lastExchangeAt ?? 0) + timing.idleWindowMs;
  const forceAt =
    timing.maxHoldMs == null ? Number.POSITIVE_INFINITY : front.enqueuedAt + timing.maxHoldMs;
  const targetAt = Math.min(idleReadyAt, forceAt);

  return targetAt <= now ? { drain: sorted } : { wakeAt: targetAt };
};

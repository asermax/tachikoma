import type { Delivery } from "../../channels/types.ts";
import type { Logger } from "../../log.ts";
import { formatDigest, formatNotification } from "./format.ts";
import { type NotifyPayload, parseNotifyPayload, SEVERITIES } from "./payload.ts";

export interface RouterOptions {
  deliver: (delivery: Delivery) => void;
  /** How long non-urgent notices accumulate before flushing as one block. */
  flushWindowSeconds: number;
  /** Forwarded to the idle gate so held notices are force-delivered eventually. */
  maxHoldSeconds: number;
  log: Logger;
  now?: () => Date;
}

/**
 * Severity router for `"notify"` events: urgent notices bypass gating and deliver
 * immediately; everything else accumulates over a short flush window and goes out
 * idle-gated — as a single notification or, when several piled up, as one digest.
 */
export class NotificationRouter {
  private readonly deliver: (delivery: Delivery) => void;
  private readonly flushWindowSeconds: number;
  private readonly maxHoldSeconds: number;
  private readonly log: Logger;
  private readonly now: () => Date;

  private pending: NotifyPayload[] = [];
  private timer: NodeJS.Timeout | null = null;

  constructor({ deliver, flushWindowSeconds, maxHoldSeconds, log, now }: RouterOptions) {
    this.deliver = deliver;
    this.flushWindowSeconds = flushWindowSeconds;
    this.maxHoldSeconds = maxHoldSeconds;
    this.log = log;
    this.now = now ?? (() => new Date());
  }

  handle(payload: unknown): void {
    const parsed = parseNotifyPayload(payload);

    if (parsed == null) {
      // Other extensions emit non-notification signals on this event (e.g. task
      // status objects) — those are not for the user, so skip them quietly.
      this.log.debug({ payload }, "ignoring notify event without notification text");
      return;
    }

    if (parsed.severity === SEVERITIES.urgent) {
      this.deliver({ text: formatNotification(parsed, this.now()), gate: "immediate" });
      return;
    }

    this.pending.push(parsed);

    if (this.timer == null) {
      this.timer = setTimeout(() => this.flush(), this.flushWindowSeconds * 1000);
      this.timer.unref();
    }
  }

  /** Deliver everything accumulated so far as one idle-gated block. */
  flush(): void {
    if (this.timer != null) clearTimeout(this.timer);
    this.timer = null;

    const items = this.pending;
    this.pending = [];

    const [single] = items;

    if (single == null) return;

    const text =
      items.length === 1 ? formatNotification(single, this.now()) : formatDigest(items, this.now());

    this.deliver({ text, gate: "idle", maxHoldSeconds: this.maxHoldSeconds });
  }
}

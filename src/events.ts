import type { Logger } from "./log.ts";

export type EventHandler<T> = (payload: T) => void | Promise<void>;

// ---- cross-extension event contracts -----------------------------------------
// Namespaced event names emitted across extensions, defined here so emitters and subscribers share one
// constant instead of a stringly-typed coupling. The coordinator owns the `session:*` lifecycle events
// (`session:opened`, `session:closed`, `session:post-processed`); the constants below are emitted by
// individual extensions (each doc comment says which).

/** A genuine topic change started a fresh branch on the daily trunk (not a tangent/checkpoint). */
export const SESSION_TOPIC_CHANGED_EVENT = "session:topic-changed";

/** What kind of action started the new branch. */
export type TopicChangedReason = "auto-shift" | "/new" | "earlier-branch";

export interface TopicChangedEvent {
  reason: TopicChangedReason;
}

/**
 * App event channel for user-facing notifications from any extension. The
 * notifications extension subscribes and routes these to the configured
 * channels. Payloads are validated leniently on the receiving side
 * (`parseNotifyPayload` in `extensions/notifications/payload.ts`).
 */
export const NOTIFY_EVENT = "notify";

export const SEVERITIES = {
  info: "info",
  warning: "warning",
  urgent: "urgent",
} as const;

export type Severity = keyof typeof SEVERITIES;

export interface NotifyPayload {
  title?: string;
  text: string;
  severity: Severity;
  source: string;
}

/**
 * Fire-and-forget request to create an ad-hoc background task instance. The
 * tasks extension subscribes, validates the payload, and creates the pending
 * instance (`definitionId: null`); its existing tick dispatches the run.
 */
export const DISPATCH_BACKGROUND_TASK_EVENT = "task:dispatch-background";

export interface DispatchBackgroundTaskPayload {
  prompt: string;
  goal?: string;
  source: string;
}

export class EventBus {
  private readonly handlers = new Map<string, Set<EventHandler<never>>>();
  private readonly log: Logger;

  constructor(log: Logger) {
    this.log = log;
  }

  on<T = unknown>(event: string, handler: EventHandler<T>): () => void {
    const set = this.handlers.get(event) ?? new Set();
    set.add(handler as EventHandler<never>);
    this.handlers.set(event, set);

    return () => {
      set.delete(handler as EventHandler<never>);
    };
  }

  emit<T = unknown>(event: string, payload: T): void {
    const set = this.handlers.get(event);
    if (set == null) return;

    for (const handler of set) {
      // Handlers are isolated: one failing subscriber never affects the others.
      void Promise.resolve()
        .then(() => (handler as EventHandler<T>)(payload))
        .catch((error) => this.log.error({ event, err: error }, "event handler failed"));
    }
  }
}

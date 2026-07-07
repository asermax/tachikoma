import type { Logger } from "./log.ts";

export type EventHandler<T> = (payload: T) => void | Promise<void>;

// ---- cross-extension event contracts -----------------------------------------
// Namespaced event names emitted across extensions, defined here so emitters and subscribers share one
// constant instead of a stringly-typed coupling. The coordinator owns the `session:*` lifecycle events
// (`session:opened`, `session:closed`, `session:post-processed`); the one below is emitted by the
// boundary extension.

/** A genuine topic change started a fresh branch on the daily trunk (not a tangent/checkpoint). */
export const SESSION_TOPIC_CHANGED_EVENT = "session:topic-changed";

/** What kind of action started the new branch. */
export type TopicChangedReason = "auto-shift" | "/new" | "earlier-branch";

export interface TopicChangedEvent {
  reason: TopicChangedReason;
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

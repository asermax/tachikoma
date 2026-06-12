import type { Logger } from "./log.ts";

export type EventHandler<T> = (payload: T) => void | Promise<void>;

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

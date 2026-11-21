import type { ButtonSpec } from "./buttons.ts";

/**
 * Live selection state for one multi-select prompt: the option layout as sent (rows preserved so
 * re-renders keep the agent's layout) plus the values currently selected.
 */
export interface MultiSelectState {
  rows: ButtonSpec[][];
  selected: Set<string>;
}

/** The outcome of a Done tap: submit the selection, recognize a duplicate, or degrade. */
export type FinishResult =
  | { status: "submitted"; values: string[] }
  | { status: "duplicate" }
  | { status: "unknown" };

/** The outcome of an option tap: the toggled live state, a first tap on lost state, or a drop. */
export type ToggleResult =
  | { status: "toggled"; state: MultiSelectState }
  | { status: "unknown" }
  | { status: "dropped" };

/**
 * Bounds registry growth: this is a single-user chat, so more than a handful of concurrently
 * open multi-select prompts is unrealistic — 25 covers it while capping memory from abandoned
 * prompts (each entry is at most 99 small label/value pairs). The finished set shares the cap;
 * forgetting an ancient finished id only means its (long-gone) keyboard would degrade once.
 */
const REGISTRY_CAP = 25;

/** Anything keyed by message id that must not grow without bound. */
interface NumberKeyed {
  readonly size: number;
  keys(): IterableIterator<number>;
  delete(key: number): unknown;
}

/** Evict the oldest key beyond the cap (Map/Set iterate in insertion order). */
const evictOldest = (store: NumberKeyed): void => {
  while (store.size > REGISTRY_CAP) {
    const oldest = store.keys().next().value;
    if (oldest === undefined) break;
    store.delete(oldest);
  }
};

/**
 * Per-message selection state for multi-select button prompts, shared between the tool (seeds on
 * send) and the channel (toggles and submits on tap). Selection state is deliberately in-memory:
 * its useful lifetime is one prompt, and a lost state degrades to an explicit agent-facing turn
 * (see the channel's degraded tap paths) rather than silent misbehavior. Every mutation is
 * synchronous — a read-modify-write completes within one JS turn, so even concurrently invoked
 * tap handlers can never lose a toggle.
 */
export class MultiSelectRegistry {
  private readonly live = new Map<number, MultiSelectState>();
  private readonly finished = new Set<number>();

  /** Record a freshly sent multi-select prompt; evicts the oldest never-submitted prompt beyond the cap. */
  seed(messageId: number, rows: ButtonSpec[][]): void {
    this.live.set(messageId, { rows, selected: new Set() });
    evictOldest(this.live);
  }

  /**
   * Toggle a value's selection, owning the tap lifecycle the way `finish` owns Done's: a live
   * entry toggles and yields the live state (authoritative at render time; a value not among
   * the prompt's options no-ops rather than corrupting the selection); a finished prompt is
   * `dropped` (acked, nothing submitted); a prompt with no registered state (restart, eviction)
   * is claimed as finished and reported `unknown`, so only the first such tap degrades.
   */
  toggle(messageId: number, value: string): ToggleResult {
    if (this.finished.has(messageId)) return { status: "dropped" };

    const state = this.live.get(messageId);
    if (state == null) {
      return this.markFinished(messageId) ? { status: "unknown" } : { status: "dropped" };
    }

    const known = state.rows.flat().some((option) => option.value === value);
    if (known) {
      if (state.selected.has(value)) {
        state.selected.delete(value);
      } else {
        state.selected.add(value);
      }
    }

    return { status: "toggled", state };
  }

  /**
   * Submit the prompt: a live entry yields its selected values in displayed order and is removed
   * (the message is recorded as finished, so a repeated Done tap is a recognizable duplicate);
   * an already-finished message reports `duplicate`; a never-seen message reports `unknown` and
   * is claimed as finished so only the first degraded tap proceeds.
   */
  finish(messageId: number): FinishResult {
    const state = this.live.get(messageId);
    if (state != null) {
      this.live.delete(messageId);
      this.markFinished(messageId);
      return {
        status: "submitted",
        values: state.rows
          .flat()
          .filter((option) => state.selected.has(option.value))
          .map((option) => option.value),
      };
    }

    if (this.finished.has(messageId)) return { status: "duplicate" };

    this.markFinished(messageId);
    return { status: "unknown" };
  }

  /** Whether the prompt was already submitted (or degraded) — later taps are acked and dropped. */
  isFinished(messageId: number): boolean {
    return this.finished.has(messageId);
  }

  /**
   * Mark a prompt terminal. Returns whether this call newly marked it — `toggle`/`finish` use
   * the return as a claim so concurrent taps on a stateless message submit one turn, not many.
   */
  private markFinished(messageId: number): boolean {
    if (this.finished.has(messageId)) return false;

    this.finished.add(messageId);
    evictOldest(this.finished);
    return true;
  }
}

import type { Logger } from "../log.ts";

export interface DebouncedTask {
  /** Reset the debounce timer (call on each signal). No-op when disabled (delay <= 0). */
  touch(): void;
  /** Cancel any pending fire and forget any coalesced re-run. An in-flight run is left to complete. */
  clear(): void;
  /** Resolves once no run is in flight (test seam / orderly shutdown drain). */
  whenIdle(): Promise<void>;
}

/**
 * Trailing-edge debounce over an async `task`. Every `touch()` clears and re-arms a
 * `delayMs` timer; the task runs once — in the background — only after `delayMs`
 * elapses with no further `touch()`. Execution is single-flighted with coalescing:
 * a fire that becomes due while a previous run is still active defers to a single
 * re-run after that run completes, so touches never overlap executions yet are
 * never silently lost. `delayMs <= 0` disables the task entirely (`touch()` is a
 * no-op). The timer is `unref()`-ed so it never keeps the process alive on its own;
 * callers that need an in-flight run to finish (e.g. a finalize backstop) await
 * `whenIdle()`. The `task` is expected to be error-tolerant — any rejection is
 * warned and never propagated.
 */
export const createDebouncedTask = (
  task: () => Promise<void>,
  delayMs: number,
  log: Logger,
): DebouncedTask => {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running: Promise<void> | null = null;
  // A fire became due while a run was active — coalesced into one re-run after it.
  let pending = false;

  const start = (): void => {
    running = Promise.resolve()
      .then(task)
      .catch((err) => log.warn({ err }, "debounced task failed"))
      .finally(() => {
        running = null;
        if (pending) {
          pending = false;
          start();
        }
      });
  };

  const fire = (): void => {
    timer = null;
    if (running != null) {
      // A previous run is still active — defer a single coalesced re-run.
      pending = true;
      return;
    }
    start();
  };

  return {
    touch() {
      if (delayMs <= 0) return;
      if (timer != null) clearTimeout(timer);
      timer = setTimeout(fire, delayMs);
      timer.unref();
    },

    clear() {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
      pending = false;
    },

    async whenIdle() {
      // Re-check after each await: a coalesced re-run reassigns `running`.
      while (running != null) {
        await running;
      }
    },
  };
};

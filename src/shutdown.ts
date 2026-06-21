import type { Logger } from "./log.ts";

export type ShutdownCause = "SIGINT" | "SIGTERM" | "uncaughtException" | "unhandledRejection";

/** Exit causes that leave no external killer: an autonomous process death needs a self-bound exit. */
const CRASH_CAUSES: ReadonlySet<ShutdownCause> = new Set([
  "uncaughtException",
  "unhandledRejection",
]);

/**
 * Force-exit backstop for the crash drain. Generous because recovery (re-running post-processing
 * for any session whose `postProcessingState` is null) backstops whatever the drain does not
 * finish in time — so the exact value is non-critical.
 */
export const SHUTDOWN_FORCE_EXIT_MS = 180_000;

export interface ShutdownDeps {
  /** The controller whose signal drives the coordinator loop's graceful drain. */
  abort: AbortController;
  log: Logger;
  /**
   * Override the exit primitive so tests do not terminate vitest. Defaults to `process.exit`.
   * Only the timeout/second-signal backstop and a repeated trigger call this; the normal
   * crash-drain completion sets `process.exitCode` in app.ts instead.
   */
  exit?: (code: number) => void;
}

/**
 * Routes every process-exit cause — graceful signals AND uncaught errors — through one
 * idempotent graceful-drain path. The first `trigger()` aborts the controller (the
 * coordinator's `run()` finally then drains the held queue to the channel — the trunk is left
 * open, not closed); a crash cause also arms an unref'd force-exit timer so an unattended
 * process still exits and restarts even if the drain hangs. A second `trigger()` while a drain
 * is in progress force-exits immediately, so a wedged drain is always escapable.
 *
 * `didCrash` tells app.ts to exit non-zero once the drain completes (it sets `process.exitCode`
 * rather than calling `process.exit`, so pino's file stream still flushes).
 */
export class ShutdownController {
  private started = false;
  private crashed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly deps: ShutdownDeps;

  constructor(deps: ShutdownDeps) {
    this.deps = deps;
  }

  /** True once a crash cause fired — callers use this to set a non-zero exit code. */
  get didCrash(): boolean {
    return this.crashed;
  }

  trigger(cause: ShutdownCause, detail?: unknown): void {
    if (this.started) {
      // A drain is already running — a second attempt means "stop waiting, exit now".
      this.clearTimer();
      this.deps.log.warn({ cause }, "force exit — shutdown already in progress");
      this.exit(1);
      return;
    }

    this.started = true;

    if (CRASH_CAUSES.has(cause)) {
      this.crashed = true;
      this.deps.log.error({ cause, err: detail }, "unrecoverable error — draining before exit");
    } else {
      this.deps.log.info({ cause }, "shutting down");
    }

    this.deps.abort.abort();

    if (this.crashed) {
      this.timer = setTimeout(() => {
        this.deps.log.warn({ cause }, "drain timed out — force exit");
        this.exit(1);
      }, SHUTDOWN_FORCE_EXIT_MS);
      this.timer.unref();
    }
  }

  private exit(code: number): void {
    (this.deps.exit ?? process.exit)(code);
  }

  private clearTimer(): void {
    if (this.timer != null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

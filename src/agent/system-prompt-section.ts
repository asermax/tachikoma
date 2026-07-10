import type { ExtensionContext, ExtensionFactory } from "@earendil-works/pi-coding-agent";

/** Default refresh window for {@link provideDebouncedContext}: 30 minutes. */
export const DEFAULT_CONTEXT_REFRESH_MS = 30 * 60 * 1000;

export interface ProvideContextOptions {
  /**
   * Refresh window for the hidden-message (`customType`) mode. Once injected, further turns within
   * the window do not re-inject; the first turn at/after the boundary triggers a fresh injection
   * (content re-resolved from `provide`). Only meaningful with a `customType`; `<= 0` (or omitted)
   * keeps the default once-per-session behavior.
   */
  debounceMs?: number;
}

/**
 * Content for a context section: a static string, or a function (given the session ctx, optionally
 * async) for content that depends on session/workspace state.
 */
export type ContextProvider = string | ((ctx: ExtensionContext) => string | Promise<string>);

const resolve = async (provide: ContextProvider, ctx: ExtensionContext): Promise<string> =>
  (typeof provide === "function" ? await provide(ctx) : provide).trim();

/**
 * Whether a debounced hidden-message injection is due. `null` (never injected) is always due;
 * otherwise due once `now - lastInjectedAt >= debounceMs` (the boundary itself counts as due). Pure
 * so the gate is unit-testable without timers. See {@link provideContext} `debounceMs`.
 */
export const dueForInjection = (
  lastInjectedAt: number | null,
  now: number,
  debounceMs: number,
): boolean => lastInjectedAt == null || now - lastInjectedAt >= debounceMs;

/**
 * Contribute context to an agent through pi's native `before_agent_start` hook (the documented "make
 * it visible to the model" step, where `provide` can read the workspace and run async fs/git work).
 * We deliberately do NOT prepare on `session_start` — that event does not fire ahead of the first
 * agent start for the sessions Tachikoma opens via `createAgentSession`. Two delivery modes:
 *
 *  - no `customType` → append `content` to the turn's system prompt. The override is rebuilt per
 *    agent start, so the append must re-fire every turn; `provide` runs once and the result is
 *    cached, preserving a per-session snapshot while keeping the append present on every turn.
 *  - with `customType` → inject `content` as a hidden, persisted message (stored in the
 *    transcript, sent to the LLM, never shown to the user). By default this happens **once per
 *    session**; pass `options.debounceMs` (`> 0`) to instead re-inject on a refresh window — the
 *    first turn injects, further turns within the window are suppressed, and the next turn at/after
 *    the boundary re-injects with content re-resolved from `provide` (a leading-edge refresh-rate
 *    limiter, distinct from DES-007's trailing-edge coalescing debounce).
 *
 * Empty content contributes nothing and stays eligible for a later turn (it does not consume the
 * debounce window). Content is never XML-wrapped.
 *
 * Bound through `app.agent.use(..., { sessionScopes })`, so a section reaches exactly the agents
 * whose scope includes it.
 *
 * @see provideDebouncedContext for the hidden-message mode with the 30-minute default baked in.
 */
export const provideContext =
  (
    provide: ContextProvider,
    customType?: string,
    options?: ProvideContextOptions,
  ): ExtensionFactory =>
  (pi) => {
    // Resolve the refresh window once: a positive `debounceMs` re-injects after that many ms;
    // anything else (omitted or `<= 0`) means once-per-session, expressed as an infinite window so
    // the same `dueForInjection` gate serves both modes. State lives in this factory closure, which
    // is recreated per session, so it spans a trunk's lifetime and resets on a new trunk.
    const windowMs =
      options?.debounceMs != null && options.debounceMs > 0 ? options.debounceMs : Infinity;
    let cached: string | null = null;
    let lastInjectedAt: number | null = null;

    pi.on("before_agent_start", async (event, ctx) => {
      if (customType != null) {
        const now = Date.now();
        if (!dueForInjection(lastInjectedAt, now, windowMs)) return undefined;

        const content = await resolve(provide, ctx);
        if (content === "") return undefined;

        lastInjectedAt = now;
        return { message: { customType, content, display: false } };
      }

      if (cached == null) {
        const content = await resolve(provide, ctx);
        if (content === "") return undefined;

        cached = content;
      }

      return { systemPrompt: `${event.systemPrompt}\n\n${cached}` };
    });
  };

/**
 * Contribute a hidden, persisted context message (`customType`) re-injected at most once per
 * `debounceMs` window (default {@link DEFAULT_CONTEXT_REFRESH_MS}, 30 minutes) — a refresh-rate
 * limiter over {@link provideContext}'s hidden-message mode. Content is re-resolved from `provide`
 * on each refresh, so a `provide` that reads live state carries fresh content every window. Thin
 * wrapper delegating to {@link provideContext}; pass `debounceMs <= 0` for once-per-session.
 */
export const provideDebouncedContext = (
  provide: ContextProvider,
  customType: string,
  debounceMs: number = DEFAULT_CONTEXT_REFRESH_MS,
): ExtensionFactory => provideContext(provide, customType, { debounceMs });

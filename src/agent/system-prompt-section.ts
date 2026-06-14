import type { ExtensionContext, ExtensionFactory } from "@earendil-works/pi-coding-agent";

/**
 * Content for a context section: a static string, or a function (given the session ctx, optionally
 * async) for content that depends on session/workspace state.
 */
export type ContextProvider = string | ((ctx: ExtensionContext) => string | Promise<string>);

const resolve = async (provide: ContextProvider, ctx: ExtensionContext): Promise<string> =>
  (typeof provide === "function" ? await provide(ctx) : provide).trim();

/**
 * Contribute context to an agent through pi's native `before_agent_start` hook (the documented "make
 * it visible to the model" step, where `provide` can read the workspace and run async fs/git work).
 * We deliberately do NOT prepare on `session_start` — that event does not fire ahead of the first
 * agent start for the sessions Tachikoma opens via `createAgentSession`. Two delivery modes:
 *
 *  - no `customType` → append `content` to the turn's system prompt. The override is rebuilt per
 *    agent start, so the append must re-fire every turn; `provide` runs once and the result is
 *    cached, preserving a per-session snapshot while keeping the append present on every turn.
 *  - with `customType` → inject `content` once as a hidden, persisted message (stored in the
 *    transcript, sent to the LLM, never shown to the user).
 *
 * Empty content contributes nothing and stays eligible for a later turn. Content is never XML-wrapped.
 *
 * Bound through `app.agent.use(..., { sessionScopes })`, so a section reaches exactly the agents
 * whose scope includes it.
 */
export const provideContext =
  (provide: ContextProvider, customType?: string): ExtensionFactory =>
  (pi) => {
    let cached: string | null = null;
    let injected = false;

    pi.on("before_agent_start", async (event, ctx) => {
      if (customType != null) {
        if (injected) return undefined;

        const content = await resolve(provide, ctx);
        if (content === "") return undefined;

        injected = true;

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

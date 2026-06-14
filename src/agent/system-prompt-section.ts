import type { ExtensionContext, ExtensionFactory } from "@earendil-works/pi-coding-agent";

/**
 * A pi extension factory that injects a context block ONCE per session as a persisted,
 * hidden message — stored in the session transcript, sent to the LLM, never shown to the
 * user. The block is prepared on `session_start` (so `provide` can read the session ctx
 * and run async fs/git work) and injected on the first `before_agent_start` (pi's documented
 * "make it visible to the model" step). Empty content yields no injection.
 *
 * Bound through `app.agent.use(..., { sessionScopes })`, so a section reaches exactly the
 * agents whose scope includes it — the background task agent inherits a section only when it
 * is bound to `"background"`, keeping guidance aligned with where the tools actually exist.
 */
export interface PersistentContextSectionOptions {
  /**
   * The section content: a static string, or a function (run on session_start, given the
   * session ctx, optionally async) for content that depends on session/workspace state.
   */
  provide: string | ((ctx: ExtensionContext) => string | Promise<string>);
}

export const persistentContextSection =
  (customType: string, { provide }: PersistentContextSectionOptions): ExtensionFactory =>
  (pi) => {
    let content = "";

    pi.on("session_start", async (_event, ctx) => {
      content = (typeof provide === "function" ? await provide(ctx) : provide).trim();
    });

    let injected = false;

    pi.on("before_agent_start", () => {
      if (injected || content === "") return undefined;

      injected = true;

      return {
        message: {
          customType,
          content: `<context owner="${customType}">\n${content}\n</context>`,
          display: false,
        },
      };
    });
  };

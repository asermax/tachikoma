import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { ExchangeProcessor, SessionsApi } from "../api.ts";

const SUMMARY_SYSTEM = `You maintain a rolling one-paragraph summary of a conversation session.

Given the previous summary (possibly empty) and the latest exchange, produce an updated
summary capturing the session's topic and current state. Keep it under 80 words, factual,
no preamble.`;

const MAX_EXCHANGE_CHARS = 2000;

const clip = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, max)}…`;

export type Completer = Pick<SideRunner, "complete">;

export const createSummaryProcessor = (
  side: Completer,
  sessions: SessionsApi,
  log: Logger,
): ExchangeProcessor => ({
  name: "rolling-summary",

  async process({ session, userText, assistantText }) {
    const lastExchange = clip(`user: ${userText}\nassistant: ${assistantText}`, MAX_EXCHANGE_CHARS);

    try {
      const summary = await side.complete({
        tier: "processor",
        system: SUMMARY_SYSTEM,
        user: [
          session.summary != null
            ? `Previous summary:\n${session.summary}`
            : "Previous summary: (none)",
          `Latest exchange:\n${lastExchange}`,
        ].join("\n\n"),
      });

      sessions.update(session.id, { summary: clip(summary.trim(), 600), lastExchange });
    } catch (error) {
      // Keep the verbatim exchange even when summarization fails — the boundary
      // detector can still work with it on the next message.
      log.error({ err: error }, "rolling summary failed");
      sessions.update(session.id, { lastExchange });
    }
  },
});

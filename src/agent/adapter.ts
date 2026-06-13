import type { AssistantMessage, Usage } from "@earendil-works/pi-ai";
import type { AgentSession } from "@earendil-works/pi-coding-agent";

import type { AgentEvent, ResultUsage } from "../domain/agent-events.ts";
import { classifyError } from "./errors.ts";
import { sanitizeText } from "./sanitize.ts";

type SessionEvent = Parameters<Parameters<AgentSession["subscribe"]>[0]>[0];

type AgentEndEvent = Extract<SessionEvent, { type: "agent_end" }>;

const EMPTY_USAGE: Usage = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

const isAssistantMessage = (message: unknown): message is AssistantMessage =>
  typeof message === "object" &&
  message != null &&
  (message as { role?: string }).role === "assistant";

/** Sum token and cost usage across the assistant turns produced in one run. */
const collectUsage = (messages: AgentEndEvent["messages"]): ResultUsage => {
  const usage: Usage = structuredClone(EMPTY_USAGE);

  for (const message of messages) {
    if (!isAssistantMessage(message)) continue;

    const turn = message.usage;
    usage.input += turn.input;
    usage.output += turn.output;
    usage.cacheRead += turn.cacheRead;
    usage.cacheWrite += turn.cacheWrite;
    usage.totalTokens += turn.totalTokens;
    usage.cost.input += turn.cost.input;
    usage.cost.output += turn.cost.output;
    usage.cost.cacheRead += turn.cost.cacheRead;
    usage.cost.cacheWrite += turn.cost.cacheWrite;
    usage.cost.total += turn.cost.total;
  }

  return { costUsd: usage.cost.total, usage };
};

const mapSessionEvent = (event: SessionEvent): AgentEvent | null => {
  switch (event.type) {
    case "message_update": {
      const update = event.assistantMessageEvent;

      if (update.type === "text_delta") return { kind: "text", text: sanitizeText(update.delta) };
      if (update.type === "thinking_delta")
        return { kind: "thinking", text: sanitizeText(update.delta) };

      return null;
    }

    case "tool_execution_start":
      return {
        kind: "tool-start",
        toolCallId: event.toolCallId,
        toolName: event.toolName,
        args: (event.args ?? {}) as Record<string, unknown>,
      };

    case "tool_execution_end":
      return {
        kind: "tool-end",
        toolCallId: event.toolCallId,
        toolName: event.toolName,
        isError: event.isError,
      };

    case "compaction_start":
      return { kind: "status", text: "Compacting conversation…" };

    case "auto_retry_start":
      return { kind: "status", text: "Provider hiccup — retrying…" };

    default:
      return null;
  }
};

/**
 * Run one prompt against a pi session, exposing the stream as domain AgentEvents.
 * The iterator completes only after pi's prompt() promise settles, so consumers
 * can treat iteration end as exchange end.
 */
export const streamPrompt = (session: AgentSession, text: string): AsyncIterable<AgentEvent> => {
  const queue: AgentEvent[] = [];
  let wake: (() => void) | null = null;
  let finished = false;
  let failure: unknown = null;
  let lastResult: ResultUsage | null = null;

  const push = (event: AgentEvent | null) => {
    if (event != null) queue.push(event);
    wake?.();
    wake = null;
  };

  const unsubscribe = session.subscribe((event) => {
    // The final agent_end of a run (not a retry boundary) carries the run's
    // messages, the only place pi surfaces per-exchange token/cost totals.
    if (event.type === "agent_end" && !event.willRetry) {
      lastResult = collectUsage(event.messages);
    }

    push(mapSessionEvent(event));
  });

  const run = session
    .prompt(text)
    .catch((error) => {
      failure = error;
    })
    .finally(() => {
      finished = true;
      push(null);
    });

  return (async function* () {
    try {
      while (true) {
        while (queue.length > 0) yield queue.shift() as AgentEvent;

        if (finished) break;

        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }

      if (failure != null) {
        const message = sanitizeText(failure instanceof Error ? failure.message : String(failure));

        yield { kind: "error", message, ...classifyError(message) };
      } else {
        yield {
          kind: "result",
          stopReason: "done",
          sessionId: session.sessionId,
          ...(lastResult != null ? { result: lastResult } : {}),
        };
      }
    } finally {
      unsubscribe();
      await run;
    }
  })();
};

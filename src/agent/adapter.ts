import type { AgentSession } from "@earendil-works/pi-coding-agent";

import type { AgentEvent } from "../domain/agent-events.ts";

type SessionEvent = Parameters<Parameters<AgentSession["subscribe"]>[0]>[0];

const mapSessionEvent = (event: SessionEvent): AgentEvent | null => {
  switch (event.type) {
    case "message_update": {
      const update = event.assistantMessageEvent;

      if (update.type === "text_delta") return { kind: "text", text: update.delta };
      if (update.type === "thinking_delta") return { kind: "thinking", text: update.delta };

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

  const push = (event: AgentEvent | null) => {
    if (event != null) queue.push(event);
    wake?.();
    wake = null;
  };

  const unsubscribe = session.subscribe((event) => push(mapSessionEvent(event)));

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
        yield {
          kind: "error",
          message: failure instanceof Error ? failure.message : String(failure),
        };
      } else {
        yield { kind: "result", stopReason: "done" };
      }
    } finally {
      unsubscribe();
      await run;
    }
  })();
};

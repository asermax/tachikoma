import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { streamPrompt } from "../src/agent/adapter.ts";
import type { AgentEvent } from "../src/domain/agent-events.ts";

type Listener = (event: unknown) => void;

const fakeSession = (script: (emit: Listener) => Promise<void>) => {
  const listeners = new Set<Listener>();

  return {
    subscribe(listener: Listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    prompt: () =>
      script((event) => {
        for (const listener of listeners) listener(event);
      }),
  } as unknown as AgentSession;
};

const collect = async (events: AsyncIterable<AgentEvent>): Promise<AgentEvent[]> => {
  const all: AgentEvent[] = [];
  for await (const event of events) all.push(event);
  return all;
};

describe("streamPrompt", () => {
  it("maps pi session events to domain events and ends with a result", async () => {
    const session = fakeSession(async (emit) => {
      emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "Hel" } });
      emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "lo" } });
      emit({
        type: "tool_execution_start",
        toolCallId: "t1",
        toolName: "read",
        args: { path: "x" },
      });
      emit({ type: "tool_execution_end", toolCallId: "t1", toolName: "read", isError: false });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([
      { kind: "text", text: "Hel" },
      { kind: "text", text: "lo" },
      { kind: "tool-start", toolCallId: "t1", toolName: "read", args: { path: "x" } },
      { kind: "tool-end", toolCallId: "t1", toolName: "read", isError: false },
      { kind: "result", stopReason: "done" },
    ]);
  });

  it("surfaces prompt failures as an error event", async () => {
    const session = fakeSession(async () => {
      throw new Error("provider exploded");
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([{ kind: "error", message: "provider exploded" }]);
  });
});

import type { AssistantMessage, Usage } from "@earendil-works/pi-ai";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { streamPrompt } from "../src/agent/adapter.ts";
import type { AgentEvent } from "../src/domain/agent-events.ts";

type Listener = (event: unknown) => void;

const fakeSession = (script: (emit: Listener) => Promise<void>, sessionId = "session-1") => {
  const listeners = new Set<Listener>();

  return {
    sessionId,
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

const usage = (overrides: Partial<Usage> = {}): Usage => ({
  input: 10,
  output: 20,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 30,
  cost: { input: 0.01, output: 0.02, cacheRead: 0, cacheWrite: 0, total: 0.03 },
  ...overrides,
});

const assistantMessage = (text: string, turnUsage: Usage): AssistantMessage => ({
  role: "assistant",
  content: [{ type: "text", text }],
  api: "anthropic-messages" as AssistantMessage["api"],
  provider: "anthropic" as AssistantMessage["provider"],
  model: "claude",
  usage: turnUsage,
  stopReason: "stop",
  timestamp: 0,
});

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
      { kind: "result", stopReason: "done", sessionId: "session-1" },
    ]);
  });

  it("captures session id and summed cost/usage from agent_end on the result", async () => {
    const session = fakeSession(async (emit) => {
      emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "hi" } });
      emit({
        type: "agent_end",
        willRetry: false,
        messages: [
          assistantMessage("a", usage()),
          {
            role: "toolResult",
            toolCallId: "t1",
            toolName: "read",
            content: [],
            isError: false,
            timestamp: 0,
          },
          assistantMessage(
            "b",
            usage({
              totalTokens: 5,
              cost: { input: 0, output: 0.005, cacheRead: 0, cacheWrite: 0, total: 0.005 },
            }),
          ),
        ],
      });
    }, "session-cost");

    const events = await collect(streamPrompt(session, "hi"));
    const result = events.at(-1);

    expect(result).toMatchObject({
      kind: "result",
      stopReason: "done",
      sessionId: "session-cost",
      result: {
        usage: expect.objectContaining({ totalTokens: 35, output: 40 }),
      },
    });

    if (result?.kind !== "result" || result.result == null) throw new Error("expected usage");
    expect(result.result.costUsd).toBeCloseTo(0.035, 6);
  });

  it("ignores agent_end emitted on a retry boundary", async () => {
    const session = fakeSession(async (emit) => {
      emit({
        type: "agent_end",
        willRetry: true,
        messages: [assistantMessage("partial", usage())],
      });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events.at(-1)).toEqual({ kind: "result", stopReason: "done", sessionId: "session-1" });
  });

  it("surfaces prompt failures as a classified, recoverable error event", async () => {
    const session = fakeSession(async () => {
      throw new Error("the provider is overloaded right now");
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([
      {
        kind: "error",
        message: "the provider is overloaded right now",
        recoverable: true,
        errorKind: "provider",
      },
    ]);
  });

  it("stringifies a non-Error rejection value", async () => {
    const session = fakeSession(async () => {
      throw "plain string failure";
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events[0]).toMatchObject({ kind: "error", message: "plain string failure" });
  });

  it("classifies auth failures as non-recoverable", async () => {
    const session = fakeSession(async () => {
      throw new Error("authentication failed: invalid api key");
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([
      {
        kind: "error",
        message: "authentication failed: invalid api key",
        recoverable: false,
        errorKind: "auth",
      },
    ]);
  });

  it("maps thinking deltas to thinking events and drops unknown message updates", async () => {
    const session = fakeSession(async (emit) => {
      emit({
        type: "message_update",
        assistantMessageEvent: { type: "thinking_delta", delta: "pondering" },
      });
      emit({
        type: "message_update",
        assistantMessageEvent: { type: "signature_delta", delta: "ignored" },
      });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([
      { kind: "thinking", text: "pondering" },
      { kind: "result", stopReason: "done", sessionId: "session-1" },
    ]);
  });

  it("maps compaction and auto-retry events to status updates", async () => {
    const session = fakeSession(async (emit) => {
      emit({ type: "compaction_start" });
      emit({ type: "auto_retry_start" });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([
      { kind: "status", text: "Compacting conversation…" },
      { kind: "status", text: "Provider hiccup — retrying…" },
      { kind: "result", stopReason: "done", sessionId: "session-1" },
    ]);
  });

  it("drops unrecognized session events", async () => {
    const session = fakeSession(async (emit) => {
      emit({ type: "agent_start" });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events).toEqual([{ kind: "result", stopReason: "done", sessionId: "session-1" }]);
  });

  it("defaults tool-start args to an empty object when absent", async () => {
    const session = fakeSession(async (emit) => {
      emit({ type: "tool_execution_start", toolCallId: "t9", toolName: "noop" });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events[0]).toEqual({
      kind: "tool-start",
      toolCallId: "t9",
      toolName: "noop",
      args: {},
    });
  });

  it("sanitizes lone surrogates out of streamed text", async () => {
    const session = fakeSession(async (emit) => {
      emit({
        type: "message_update",
        assistantMessageEvent: { type: "text_delta", delta: "ok\uD800done" },
      });
    });

    const events = await collect(streamPrompt(session, "hi"));

    expect(events[0]).toEqual({ kind: "text", text: "okdone" });
  });
});

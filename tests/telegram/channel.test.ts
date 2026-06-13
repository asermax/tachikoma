import type { Bot } from "grammy";
import { describe, expect, it, vi } from "vitest";

import type { ChannelRuntime } from "../../src/channels/types.ts";
import type { AgentEvent } from "../../src/domain/agent-events.ts";
import { textMessage } from "../../src/domain/message.ts";
import {
  STOP_ACKNOWLEDGEMENT,
  STOP_COMMAND,
  TelegramChannel,
  type TelegramChannelOptions,
} from "../../src/extensions/telegram/channel.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  child: vi.fn(),
} as unknown as Logger;

interface ApiCall {
  type: "send" | "edit" | "delete" | "action";
  messageId?: number;
  text?: string;
}

type UpdateHandler = (ctx: unknown) => Promise<void> | void;

const makeChannel = (overrides: Partial<TelegramChannelOptions> = {}) => {
  const handlers = new Map<string, UpdateHandler>();
  const calls: ApiCall[] = [];
  let next = 0;

  const api = {
    sendMessage: vi.fn(async (_chat: number, text: string) => {
      calls.push({ type: "send", text });
      next += 1;
      return { message_id: next };
    }),
    editMessageText: vi.fn(async (_chat: number, messageId: number, text: string) => {
      calls.push({ type: "edit", messageId, text });
      return true;
    }),
    deleteMessage: vi.fn(async (_chat: number, messageId: number) => {
      calls.push({ type: "delete", messageId });
      return true;
    }),
    sendChatAction: vi.fn(async () => {
      calls.push({ type: "action" });
      return true;
    }),
    copyMessage: vi.fn(async () => {
      next += 1;
      return { message_id: next };
    }),
  };

  const bot = {
    api,
    token: "token",
    on: (event: string, handler: UpdateHandler) => handlers.set(event, handler),
    catch: () => {},
    init: async () => {},
    start: async () => {},
    isRunning: () => false,
    stop: async () => {},
  } as unknown as Bot;

  const stop = vi.fn(async () => {});
  const submit = vi.fn();
  const runtime: ChannelRuntime = { log: fakeLog, submit };

  const channel = new TelegramChannel(bot, {
    chatId: 42,
    allowMedia: false,
    pushNotifications: false,
    mediaDir: "/tmp/media",
    stop,
    ...overrides,
  });

  const dispatchText = async (text: string, messageId = 1) => {
    const handler = handlers.get("message");
    if (handler == null) throw new Error("message handler not registered");

    await handler({ chat: { id: 42 }, message: { message_id: messageId, text } });
  };

  return { channel, api, calls, stop, submit, runtime, dispatchText };
};

async function* stream(events: AgentEvent[]): AsyncGenerator<AgentEvent> {
  for (const event of events) yield event;
}

const pushableStream = () => {
  const queue: AgentEvent[] = [];
  let closed = false;
  let notify: (() => void) | null = null;

  const wake = () => {
    notify?.();
    notify = null;
  };

  const events = (async function* () {
    while (true) {
      const event = queue.shift();

      if (event != null) {
        yield event;
        continue;
      }

      if (closed) return;

      await new Promise<void>((resolve) => {
        notify = resolve;
      });
    }
  })();

  return {
    events,
    push: (event: AgentEvent) => {
      queue.push(event);
      wake();
    },
    end: () => {
      closed = true;
      wake();
    },
  };
};

const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

describe("/stop interception", () => {
  it("aborts the exchange and acknowledges without submitting", async () => {
    const { channel, stop, submit, runtime, dispatchText, calls } = makeChannel();
    await channel.start(runtime);

    await dispatchText(STOP_COMMAND);

    expect(stop).toHaveBeenCalledTimes(1);
    expect(submit).not.toHaveBeenCalled();
    expect(calls).toEqual([{ type: "send", text: STOP_ACKNOWLEDGEMENT }]);
  });

  it("matches /stop after trimming but not as a prefix", async () => {
    const { channel, stop, submit, runtime, dispatchText } = makeChannel();
    await channel.start(runtime);

    await dispatchText("  /stop  ");
    expect(stop).toHaveBeenCalledTimes(1);

    await dispatchText("/stop everything please");
    expect(stop).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ text: "/stop everything please" }),
    );
  });

  it("still acknowledges when the abort call fails", async () => {
    const { channel, runtime, dispatchText, calls } = makeChannel({
      stop: vi.fn(async () => {
        throw new Error("no run in flight");
      }),
    });
    await channel.start(runtime);

    await dispatchText(STOP_COMMAND);

    expect(calls).toEqual([{ type: "send", text: STOP_ACKNOWLEDGEMENT }]);
  });
});

describe("respond streaming", () => {
  it("streams text into one message and finalizes it with the full content", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello" },
        { kind: "tool-start", toolCallId: "t1", toolName: "read", args: {} },
        { kind: "text", text: " world" },
        { kind: "result", stopReason: "done" },
      ]),
    });

    const messageCalls = calls.filter((call) => call.type !== "action");
    expect(messageCalls).toEqual([
      { type: "send", text: "Hello" },
      { type: "edit", messageId: 1, text: "Hello world" },
    ]);
    expect(channel.lastOutboundMessageId).toBe(1);
  });

  it("keeps submitting inbound messages while a response is streaming", async () => {
    const { channel, runtime, submit, dispatchText } = makeChannel();
    await channel.start(runtime);

    const { events, push, end } = pushableStream();
    const responding = channel.respond({ message: textMessage("telegram", "hi"), events });

    push({ kind: "text", text: "Working…" });
    await settle();

    await dispatchText("actually, focus on the tests");
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ text: "actually, focus on the tests" }),
    );

    end();
    await responding;
  });
});

describe("status", () => {
  it("falls back to a typing action when no response is streaming", async () => {
    const { channel, runtime, api } = makeChannel();
    await channel.start(runtime);

    channel.status("Gathering context…");
    await settle();

    expect(api.sendChatAction).toHaveBeenCalledWith(42, "typing");
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it("renders through the streaming message while a response is active", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    const { events, push, end } = pushableStream();
    const responding = channel.respond({ message: textMessage("telegram", "hi"), events });
    await settle();

    channel.status("Pondering…");
    await settle();

    expect(calls.filter((call) => call.type === "send")).toEqual([
      { type: "send", text: "_Pondering…_" },
    ]);

    push({ kind: "text", text: "Answer" });
    end();
    await responding;

    expect(calls.at(-1)).toEqual({ type: "edit", messageId: 1, text: "Answer" });
  });
});

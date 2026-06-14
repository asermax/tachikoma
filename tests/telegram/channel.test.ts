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

interface RecordedMessage {
  messageId: string;
  sessionId: number;
  direction: "incoming" | "outgoing";
}

const makeChannel = (overrides: Partial<TelegramChannelOptions> = {}) => {
  const handlers = new Map<string, UpdateHandler>();
  const calls: ApiCall[] = [];
  let next = 0;

  const recorded: RecordedMessage[] = [];
  const mappings = new Map<string, number>();
  const session = { id: 100 as number | null };

  const store = {
    record: vi.fn((messageId: string, sessionId: number, direction: "incoming" | "outgoing") => {
      recorded.push({ messageId, sessionId, direction });
      mappings.set(messageId, sessionId);
    }),
    findSessionId: vi.fn((messageId: string) => mappings.get(messageId) ?? null),
  };

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
    setMyCommands: vi.fn(async () => true),
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
    store,
    currentSessionId: () => session.id,
    ...overrides,
  });

  const dispatchText = async (
    text: string,
    messageId = 1,
    replyTo?: { message_id: number; text?: string },
  ) => {
    const handler = handlers.get("message");
    if (handler == null) throw new Error("message handler not registered");

    await handler({
      chat: { id: 42 },
      message: { message_id: messageId, text, reply_to_message: replyTo },
    });
  };

  const dispatchReaction = async (messageId: number, emoji: string, userId = 42) => {
    const handler = handlers.get("message_reaction");
    if (handler == null) throw new Error("message_reaction handler not registered");

    await handler({
      chat: { id: 42 },
      messageReaction: {
        message_id: messageId,
        user: { id: userId },
        new_reaction: [{ type: "emoji", emoji }],
        old_reaction: [],
      },
    });
  };

  return {
    channel,
    api,
    calls,
    stop,
    submit,
    runtime,
    dispatchText,
    dispatchReaction,
    store,
    recorded,
    mappings,
    session,
  };
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
  it("bakes a tool marker between text segments and finalizes the full content", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello." },
        {
          kind: "tool-start",
          toolCallId: "t1",
          toolName: "read",
          args: { path: "/a/b/notes.md" },
        },
        { kind: "text", text: "Done." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    const messageCalls = calls.filter((call) => call.type !== "action");
    expect(messageCalls).toEqual([
      { type: "send", text: "Hello.\n\n_🔧 Reading /a/b/notes.md_" },
      { type: "edit", messageId: 1, text: "Hello.\n\n_🔧 Reading `notes.md`_\n\nDone." },
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

describe("shutdownStatus", () => {
  it("sends a dedicated italic message and edits it in place across calls", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    await channel.shutdownStatus("Wrapping up the conversation…");
    await channel.shutdownStatus("Post-processing: memory…");
    await channel.shutdownStatus("Done");

    expect(calls).toEqual([
      { type: "send", text: "_Wrapping up the conversation…_" },
      { type: "edit", messageId: 1, text: "_Post-processing: memory…_" },
      { type: "edit", messageId: 1, text: "_Done_" },
    ]);
  });
});

const inboundWith = (text: string, messageId: number) => ({
  text,
  channel: "telegram",
  receivedAt: new Date(),
  media: [] as never[],
  metadata: { messageId },
});

describe("message recording", () => {
  it("records the inbound and outbound message ids against the receiving session", async () => {
    const { channel, runtime, recorded } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: inboundWith("hi", 7),
      events: stream([
        { kind: "text", text: "Hello" },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(recorded).toEqual([
      { messageId: "7", sessionId: 100, direction: "incoming" },
      { messageId: "1", sessionId: 100, direction: "outgoing" },
    ]);
  });

  it("skips recording when no session is active", async () => {
    const { channel, runtime, recorded, session } = makeChannel();
    session.id = null;
    await channel.start(runtime);

    await channel.respond({
      message: inboundWith("hi", 7),
      events: stream([
        { kind: "text", text: "Hello" },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(recorded).toEqual([]);
  });
});

describe("reply-to session routing", () => {
  it("stamps the owning session as a resume target when replying to a known message", async () => {
    const { channel, runtime, submit, mappings, dispatchText } = makeChannel();
    await channel.start(runtime);

    mappings.set("3", 55);

    await dispatchText("follow-up", 9, { message_id: 3 });

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ resumeSessionId: 55 }) }),
    );
  });

  it("falls back to normal routing when the replied-to message is unknown", async () => {
    const { channel, runtime, submit, dispatchText } = makeChannel();
    await channel.start(runtime);

    await dispatchText("follow-up", 9, { message_id: 999 });

    const submitted = submit.mock.calls[0]?.[0];
    expect(submitted.metadata.resumeSessionId).toBeUndefined();
    expect(submitted.metadata.replyToMessageId).toBe("999");
  });
});

describe("inbound reactions", () => {
  it("surfaces an authorized reaction to the agent", async () => {
    const { channel, runtime, submit, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    await dispatchReaction(12, "👍");

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "The user reacted 👍 to a previous message.",
        metadata: expect.objectContaining({ reaction: true, replyToMessageId: "12" }),
      }),
    );
  });

  it("routes a reaction to the session that owns the reacted-to message", async () => {
    const { channel, runtime, submit, mappings, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    mappings.set("12", 77);

    await dispatchReaction(12, "👍");

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ resumeSessionId: 77 }) }),
    );
  });

  it("drops reactions from an unauthorized user", async () => {
    const { channel, runtime, submit, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    await dispatchReaction(12, "👍", 999);

    expect(submit).not.toHaveBeenCalled();
  });
});

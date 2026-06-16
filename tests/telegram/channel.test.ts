import { type Bot, GrammyError, HttpError } from "grammy";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChannelRuntime } from "../../src/channels/types.ts";
import type { AgentEvent } from "../../src/domain/agent-events.ts";
import { textMessage } from "../../src/domain/message.ts";
import { packCallbackData } from "../../src/extensions/telegram/buttons.ts";
import {
  STOP_ACKNOWLEDGEMENT,
  STOP_COMMAND,
  TelegramChannel,
  type TelegramChannelOptions,
  UNRESOLVED_REACTION_NOTICE,
  UNRESOLVED_REPLY_HINT,
} from "../../src/extensions/telegram/channel.ts";
import { toTelegramEntities } from "../../src/extensions/telegram/entities.ts";
import type { Logger } from "../../src/log.ts";

/** The literal display text the channel produces for a given markdown input. */
const rendered = (markdown: string): string => toTelegramEntities(markdown).text;

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  child: vi.fn(),
} as unknown as Logger;

interface ApiCall {
  type: "send" | "edit" | "delete" | "copy" | "action";
  messageId?: number;
  text?: string;
}

type UpdateHandler = (ctx: unknown) => Promise<void> | void;

interface MessageRouting {
  treeEntryId: string;
  branchId: string;
}

interface RecordedMessage {
  messageId: string;
  routing: MessageRouting;
  direction: "incoming" | "outgoing";
}

const makeChannel = (overrides: Partial<TelegramChannelOptions> = {}) => {
  const handlers = new Map<string, UpdateHandler>();
  const calls: ApiCall[] = [];
  let next = 0;

  const recorded: RecordedMessage[] = [];
  const mappings = new Map<string, MessageRouting>();
  const branchExchanges = new Map<string, string>();
  // The trunk routing for the message being produced/received: live leaf entry + live branch id.
  const routing = {
    current: { treeEntryId: "entry-1", branchId: "topic-1" } as MessageRouting | null,
  };

  const store = {
    record: vi.fn(
      (messageId: string, value: MessageRouting, direction: "incoming" | "outgoing") => {
        recorded.push({ messageId, routing: value, direction });
        mappings.set(messageId, value);
      },
    ),
    resolve: vi.fn((messageId: string) => mappings.get(messageId) ?? null),
  };

  const api = {
    sendMessage: vi.fn(async (_chat: number, text: string) => {
      calls.push({ type: "send", text });
      next += 1;
      return { message_id: next };
    }),
    copyMessage: vi.fn(async (_chat: number, _fromChat: number, messageId: number) => {
      calls.push({ type: "copy", messageId });
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
    setMyCommands: vi.fn(async () => true),
    getFile: vi.fn(async () => ({ file_path: "documents/file.bin" })),
    editMessageReplyMarkup: vi.fn(async () => true),
  };

  let errorHandler: (boundary: { error: unknown }) => void = () => {};
  let running = false;
  const startCalls: unknown[] = [];

  const bot = {
    api,
    token: "token",
    on: (event: string, handler: UpdateHandler) => handlers.set(event, handler),
    catch: (handler: (boundary: { error: unknown }) => void) => {
      errorHandler = handler;
    },
    init: async () => {},
    start: async (opts: unknown) => {
      startCalls.push(opts);
    },
    isRunning: () => running,
    stop: vi.fn(async () => {
      running = false;
    }),
  } as unknown as Bot;

  const stop = vi.fn(async () => {});
  const submit = vi.fn();
  const runtime: ChannelRuntime = { log: fakeLog, submit };

  const channel = new TelegramChannel(bot, {
    chatId: 42,
    allowMedia: false,
    pushNotifications: false,
    pushNotificationMinSeconds: 0,
    mediaDir: "/tmp/media",
    stop,
    store,
    currentRouting: () => routing.current,
    branchLastExchange: (branchId: string) => branchExchanges.get(branchId) ?? null,
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

  const dispatchReaction = async (
    messageId: number,
    emoji: string,
    userId = 42,
    chatId = 42,
    oldEmoji: string | null = null,
  ) => {
    const handler = handlers.get("message_reaction");
    if (handler == null) throw new Error("message_reaction handler not registered");

    await handler({
      chat: { id: chatId },
      messageReaction: {
        message_id: messageId,
        user: { id: userId },
        new_reaction: [{ type: "emoji", emoji }],
        old_reaction: oldEmoji == null ? [] : [{ type: "emoji", emoji: oldEmoji }],
      },
    });
  };

  const dispatchMessage = async (message: Record<string, unknown>, chatId = 42) => {
    const handler = handlers.get("message");
    if (handler == null) throw new Error("message handler not registered");

    await handler({ chat: { id: chatId }, message });
  };

  const dispatchCallback = async (
    overrides: {
      data?: string;
      fromId?: number;
      messageId?: number | null;
      answer?: () => Promise<unknown>;
    } = {},
  ) => {
    const handler = handlers.get("callback_query:data");
    if (handler == null) throw new Error("callback_query:data handler not registered");

    await handler({
      answerCallbackQuery: overrides.answer ?? (async () => true),
      callbackQuery: {
        from: { id: overrides.fromId ?? 42 },
        data: overrides.data ?? "",
        message:
          overrides.messageId === null ? undefined : { message_id: overrides.messageId ?? 5 },
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
    dispatchMessage,
    dispatchCallback,
    store,
    recorded,
    mappings,
    branchExchanges,
    routing,
    triggerError: (error: unknown) => errorHandler({ error }),
    startCalls,
    setRunning: (value: boolean) => {
      running = value;
    },
    botStop: bot.stop,
  };
};

async function* stream(events: AgentEvent[]): AsyncGenerator<AgentEvent> {
  // agent_start is always the first event — it signals that before_agent_start
  // handlers have settled and the renderer should be initialized.
  yield { kind: "agent_start" };
  for (const event of events) yield event;
}

const pushableStream = () => {
  const queue: AgentEvent[] = [{ kind: "agent_start" }];
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
      { type: "send", text: rendered("Hello.\n\n_🔧 Reading /a/b/notes.md_") },
      {
        type: "edit",
        messageId: 1,
        text: rendered("Hello.\n\n_🔧 Reading `notes.md`_\n\nDone."),
      },
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

describe("respond push notification", () => {
  it("copy-deletes the finalized message to fire one push when pushNotifications is on", async () => {
    const { channel, runtime, calls } = makeChannel({ pushNotifications: true });
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    // The streamed message (id 1) is finalized, then copied (id 2) and deleted,
    // so the fresh copy fires the push the in-place edit never could.
    expect(calls).toContainEqual({ type: "copy", messageId: 1 });
    expect(calls).toContainEqual({ type: "delete", messageId: 1 });
    expect(channel.lastOutboundMessageId).toBe(2);
  });

  it("does not copy-delete when pushNotifications is off", async () => {
    const { channel, runtime, calls } = makeChannel({ pushNotifications: false });
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(calls.filter((call) => call.type === "copy")).toEqual([]);
    expect(channel.lastOutboundMessageId).toBe(1);
  });

  it("records the copied outbound id against the receiving session", async () => {
    const { channel, runtime, recorded } = makeChannel({ pushNotifications: true });
    await channel.start(runtime);

    await channel.respond({
      message: inboundWith("hi", 7),
      events: stream([
        { kind: "text", text: "Hello" },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(recorded).toContainEqual({
      messageId: "2",
      routing: { treeEntryId: "entry-1", branchId: "topic-1" },
      direction: "outgoing",
    });
  });

  it("keeps the streamed message when the copy fails", async () => {
    const { channel, runtime, api, calls } = makeChannel({ pushNotifications: true });
    await channel.start(runtime);
    api.copyMessage.mockRejectedValue(new Error("copy not allowed"));

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    // Copy failed: no delete, the streamed message (id 1) stands, no push.
    expect(calls.filter((call) => call.type === "delete")).toEqual([]);
    expect(channel.lastOutboundMessageId).toBe(1);
  });

  it("skips the push for a quick turn under the duration threshold", async () => {
    const { channel, runtime, calls } = makeChannel({
      pushNotifications: true,
      pushNotificationMinSeconds: 60,
    });
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(calls.filter((call) => call.type === "copy")).toEqual([]);
    expect(channel.lastOutboundMessageId).toBe(1);
  });

  it("skips the push when a notifying tool (file/pin/buttons) already delivered one", async () => {
    const { channel, runtime, calls } = makeChannel({ pushNotifications: true });
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Here is the file." },
        { kind: "tool-start", toolCallId: "t1", toolName: "send_telegram_file", args: {} },
        { kind: "tool-end", toolCallId: "t1", toolName: "send_telegram_file", isError: false },
        { kind: "result", stopReason: "done" },
      ]),
    });

    // The file tool already pushed; the streamed response is left in place.
    expect(calls.filter((call) => call.type === "copy")).toEqual([]);
    expect(calls.filter((call) => call.type === "delete")).toEqual([]);
    expect(channel.lastOutboundMessageId).toBe(1);
  });

  it("copies only the last message of a multi-chunk response", async () => {
    const { channel, runtime, calls } = makeChannel({ pushNotifications: true });
    await channel.start(runtime);

    const big = "a".repeat(4200);
    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: big },
        { kind: "result", stopReason: "done" },
      ]),
    });

    // Two chunks are sent (ids 1 and 2); only the final one (id 2) is copied and
    // deleted — the first chunk stands untouched.
    const copies = calls.filter((call) => call.type === "copy");
    expect(copies).toEqual([{ type: "copy", messageId: 2 }]);
    expect(calls).toContainEqual({ type: "delete", messageId: 2 });
    expect(calls).not.toContainEqual({ type: "delete", messageId: 1 });
    expect(channel.lastOutboundMessageId).toBe(3);
  });

  it("records the streamed id (not a copied one) when the push is skipped", async () => {
    const { channel, runtime, recorded } = makeChannel({
      pushNotifications: true,
      pushNotificationMinSeconds: 60,
    });
    await channel.start(runtime);

    await channel.respond({
      message: inboundWith("hi", 7),
      events: stream([
        { kind: "text", text: "Hello" },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(recorded).toContainEqual({
      messageId: "1",
      routing: { treeEntryId: "entry-1", branchId: "topic-1" },
      direction: "outgoing",
    });
  });
});

describe("status", () => {
  it("surfaces a preparation status on a lead-in message while keeping typing alive", async () => {
    const { channel, runtime, api, calls } = makeChannel();
    await channel.start(runtime);

    channel.status("Gathering context…");
    await settle();

    expect(api.sendChatAction).toHaveBeenCalledWith(42, "typing");
    expect(calls).toContainEqual({
      type: "send",
      text: rendered("_Gathering context…_"),
    });
  });

  it("edits the lead-in message in place on a follow-up preparation status", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    channel.status("Checking conversation topic…");
    await settle();
    channel.status("Resuming a previous conversation");
    await settle();

    expect(calls).toContainEqual({
      type: "send",
      text: rendered("_Checking conversation topic…_"),
    });
    expect(calls).toContainEqual({
      type: "edit",
      messageId: 1,
      text: rendered("_Resuming a previous conversation_"),
    });
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
      { type: "send", text: rendered("_Pondering…_") },
    ]);

    push({ kind: "text", text: "Answer" });
    end();
    await responding;

    expect(calls.at(-1)).toEqual({
      type: "edit",
      messageId: 1,
      text: rendered("Answer"),
    });
  });
});

describe("preparation lead-in handoff", () => {
  it("reclaims the lead-in so streamed text edits it in place instead of sending a new message", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    channel.status("Checking conversation topic…");
    await settle();

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello paragraph one.\n\nParagraph two." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    const sends = calls.filter((call) => call.type === "send");
    expect(sends).toEqual([{ type: "send", text: rendered("_Checking conversation topic…_") }]);
    const edits = calls.filter((call) => call.type === "edit");
    expect(edits.length).toBeGreaterThan(0);
    expect(edits.every((call) => call.messageId === 1)).toBe(true);
  });

  it("deletes the lead-in message when the exchange yields no text", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    channel.status("Checking conversation topic…");
    await settle();

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([{ kind: "result", stopReason: "done" }]),
    });

    expect(calls.filter((call) => call.type === "send")).toEqual([
      { type: "send", text: rendered("_Checking conversation topic…_") },
    ]);
    expect(calls).toContainEqual({ type: "delete", messageId: 1 });
  });

  it("sends a fresh message when no lead-in was created (no preparation status)", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello paragraph one.\n\nParagraph two." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(calls.filter((call) => call.type === "send")).toEqual([
      { type: "send", text: rendered("Hello paragraph one.") },
    ]);
  });

  // Reproduces the race where status() sends the lead-in fire-and-forget: with no settle()
  // between status and respond the lead-in send is still pending when agent_start is consumed,
  // so the seed must be claimed under the send mutex or the lead-in orphans after the response.
  it("reclaims the lead-in even when its send is still in flight when the response starts", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    channel.status("Checking for relevant skills…");
    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        { kind: "text", text: "Hello paragraph one.\n\nParagraph two." },
        { kind: "result", stopReason: "done" },
      ]),
    });

    const sends = calls.filter((call) => call.type === "send");
    expect(sends).toEqual([{ type: "send", text: rendered("_Checking for relevant skills…_") }]);
    const edits = calls.filter((call) => call.type === "edit");
    expect(edits.length).toBeGreaterThan(0);
    expect(edits.every((call) => call.messageId === 1)).toBe(true);
  });

  it("deletes the in-flight lead-in when the response yields no text", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    channel.status("Checking for relevant skills…");
    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([{ kind: "result", stopReason: "done" }]),
    });

    const sends = calls.filter((call) => call.type === "send");
    expect(sends).toEqual([{ type: "send", text: rendered("_Checking for relevant skills…_") }]);
    expect(calls).toContainEqual({ type: "delete", messageId: 1 });
  });
});

describe("receipt typing", () => {
  it("fires a typing action before submitting an inbound text message", async () => {
    const { channel, runtime, api, submit, dispatchText } = makeChannel();
    await channel.start(runtime);

    await dispatchText("hello");

    expect(api.sendChatAction).toHaveBeenCalledWith(42, "typing");
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ text: "hello" }));
  });

  it("does not fire typing for a /stop message", async () => {
    const { channel, runtime, api, dispatchText } = makeChannel();
    await channel.start(runtime);

    await dispatchText(STOP_COMMAND);

    expect(api.sendChatAction).not.toHaveBeenCalled();
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
      { type: "send", text: rendered("_Wrapping up the conversation…_") },
      { type: "edit", messageId: 1, text: rendered("_Post-processing: memory…_") },
      { type: "edit", messageId: 1, text: rendered("_Done_") },
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
  it("records the inbound and outbound message ids against the current routing", async () => {
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
      {
        messageId: "7",
        routing: { treeEntryId: "entry-1", branchId: "topic-1" },
        direction: "incoming",
      },
      {
        messageId: "1",
        routing: { treeEntryId: "entry-1", branchId: "topic-1" },
        direction: "outgoing",
      },
    ]);
  });

  it("skips recording when no trunk is active", async () => {
    const { channel, runtime, recorded, routing } = makeChannel();
    routing.current = null;
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

describe("reply-to branch routing", () => {
  it("stamps the referenced branch when replying to a known message", async () => {
    const { channel, runtime, submit, mappings, dispatchText } = makeChannel();
    await channel.start(runtime);

    mappings.set("3", { treeEntryId: "entry-9", branchId: "topic-2" });

    await dispatchText("follow-up", 9, { message_id: 3 });

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        metadata: expect.objectContaining({
          forcedBranchId: "topic-2",
          forcedTreeEntryId: "entry-9",
        }),
      }),
    );
  });

  it("annotates an unresolved reply with a hint while falling back to normal routing", async () => {
    const { channel, runtime, submit, dispatchText } = makeChannel();
    await channel.start(runtime);

    await dispatchText("follow-up", 9, { message_id: 999 });

    const submitted = submit.mock.calls[0]?.[0];
    expect(submitted.metadata.forcedBranchId).toBeUndefined();
    expect(submitted.metadata.replyToMessageId).toBe("999");
    expect(submitted.text).toBe(`follow-up${UNRESOLVED_REPLY_HINT}`);
  });

  it("omits the reply quote when replying to the live branch's message", async () => {
    const { channel, runtime, submit, mappings, dispatchText } = makeChannel();
    await channel.start(runtime);

    // Target on the live branch (topic-1, the current routing): quote is suppressed (already live).
    mappings.set("3", { treeEntryId: "entry-3", branchId: "topic-1" });

    await dispatchText("follow-up", 9, { message_id: 3, text: "the plan is ready" });

    const submitted = submit.mock.calls[0]?.[0];
    expect(submitted.text).toBe("follow-up");
    expect(submitted.metadata.replyToMessageId).toBe("3");
    expect(submitted.metadata.forcedBranchId).toBe("topic-1");
  });

  it("keeps the reply quote for a message on an earlier branch", async () => {
    const { channel, runtime, submit, mappings, dispatchText } = makeChannel();
    await channel.start(runtime);

    // Target on an earlier branch (topic-2, not the live topic-1): the quote is kept for context.
    mappings.set("3", { treeEntryId: "entry-3", branchId: "topic-2" });

    await dispatchText("follow-up", 9, { message_id: 3, text: "the plan is ready" });

    const submitted = submit.mock.calls[0]?.[0];
    expect(submitted.text).toBe("Replied to:\n> the plan is ready\n\nfollow-up");
  });
});

describe("inbound reactions", () => {
  it("surfaces an authorized reaction to the agent", async () => {
    const { channel, runtime, submit, mappings, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    mappings.set("12", { treeEntryId: "entry-12", branchId: "topic-1" });

    await dispatchReaction(12, "👍");

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "The user reacted 👍 to a previous message.",
        metadata: expect.objectContaining({ reaction: true, replyToMessageId: "12" }),
      }),
    );
  });

  it("routes a reaction to the branch that owns the reacted-to message", async () => {
    const { channel, runtime, submit, mappings, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    mappings.set("12", { treeEntryId: "entry-12", branchId: "topic-2" });

    await dispatchReaction(12, "👍");

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        metadata: expect.objectContaining({
          forcedBranchId: "topic-2",
          forcedTreeEntryId: "entry-12",
        }),
      }),
    );
  });

  it("drops reactions from an unauthorized user", async () => {
    const { channel, runtime, submit, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    await dispatchReaction(12, "👍", 999);

    expect(submit).not.toHaveBeenCalled();
  });

  it("ignores reactions from a different chat", async () => {
    const { channel, runtime, submit, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    await dispatchReaction(12, "👍", 42, 999);

    expect(submit).not.toHaveBeenCalled();
  });

  it("drops a reaction that nets to no change", async () => {
    const { channel, runtime, submit, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    await dispatchReaction(12, "👍", 42, 42, "👍");

    expect(submit).not.toHaveBeenCalled();
  });

  it("prepends the referenced branch's last exchange when reacting to an earlier branch", async () => {
    const { channel, runtime, submit, mappings, branchExchanges, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    // Reaction targets a message on an earlier branch (topic-2, not the live topic-1).
    mappings.set("10", { treeEntryId: "entry-10", branchId: "topic-2" });
    branchExchanges.set("topic-2", "user: hi\nassistant: hello there");

    await dispatchReaction(10, "👍");

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "Reacted to:\nuser: hi\nassistant: hello there\n\nThe user reacted 👍 to a previous message. Interpret it in the context of the last exchange and respond accordingly.",
      }),
    );
  });

  it("omits context when reacting to a message on the live branch", async () => {
    const { channel, runtime, submit, mappings, branchExchanges, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    // Reaction targets a message on the live branch (topic-1, the current routing).
    mappings.set("11", { treeEntryId: "entry-11", branchId: "topic-1" });
    branchExchanges.set("topic-1", "user: hi\nassistant: hello there");

    await dispatchReaction(11, "👍");

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ text: "The user reacted 👍 to a previous message." }),
    );
  });

  it("drops a reaction to an unrecorded message and notifies the user", async () => {
    const { channel, runtime, submit, calls, dispatchReaction } = makeChannel();
    await channel.start(runtime);

    await dispatchReaction(404, "👍");

    expect(submit).not.toHaveBeenCalled();
    expect(calls).toContainEqual({ type: "send", text: UNRESOLVED_REACTION_NOTICE });
  });

  it("warns and drops a reaction when the unresolved-reaction notice fails to send", async () => {
    const { channel, runtime, api, submit, dispatchReaction } = makeChannel();
    api.sendMessage.mockRejectedValueOnce(new Error("send failed"));
    await channel.start(runtime);

    await dispatchReaction(404, "👍");

    expect(submit).not.toHaveBeenCalled();
    expect(fakeLog.warn).toHaveBeenCalledWith(
      expect.anything(),
      "unresolved reaction notice failed",
    );
  });
});

describe("inbound chat filtering", () => {
  it("ignores text messages from a different chat", async () => {
    const { channel, runtime, submit, dispatchMessage } = makeChannel();
    await channel.start(runtime);

    await dispatchMessage({ message_id: 1, text: "hello" }, 999);

    expect(submit).not.toHaveBeenCalled();
  });
});

describe("callback queries", () => {
  it("submits an authorized button tap and removes a single-use keyboard", async () => {
    const { channel, runtime, submit, api, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({ data: packCallbackData("approve", true), messageId: 8 });

    expect(api.editMessageReplyMarkup).toHaveBeenCalledWith(42, 8);
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ buttonValue: "approve" }) }),
    );
  });

  it("keeps a multi-use keyboard in place", async () => {
    const { channel, runtime, submit, api, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({ data: packCallbackData("more", false), messageId: 8 });

    expect(api.editMessageReplyMarkup).not.toHaveBeenCalled();
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ buttonValue: "more" }) }),
    );
  });

  it("does not attempt keyboard removal when the message id is absent", async () => {
    const { channel, runtime, submit, api, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({ data: packCallbackData("approve", true), messageId: null });

    expect(api.editMessageReplyMarkup).not.toHaveBeenCalled();
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ buttonValue: "approve" }) }),
    );
  });

  it("drops taps from an unauthorized user", async () => {
    const { channel, runtime, submit, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({ data: packCallbackData("approve", true), fromId: 999 });

    expect(submit).not.toHaveBeenCalled();
  });

  it("ignores unrecognized callback data", async () => {
    const { channel, runtime, submit, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({ data: "garbage" });

    expect(submit).not.toHaveBeenCalled();
  });

  it("warns but proceeds when answering the callback fails", async () => {
    const { channel, runtime, submit, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({
      data: packCallbackData("approve", false),
      messageId: 8,
      answer: async () => {
        throw new Error("answer failed");
      },
    });

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ buttonValue: "approve" }) }),
    );
  });

  it("warns when single-use keyboard removal fails", async () => {
    const { channel, runtime, submit, api, dispatchCallback } = makeChannel();
    api.editMessageReplyMarkup.mockRejectedValueOnce(new Error("removal failed"));
    await channel.start(runtime);

    await dispatchCallback({ data: packCallbackData("approve", true), messageId: 8 });
    await settle();

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ buttonValue: "approve" }) }),
    );
  });

  it("forces the referenced branch when tapping a button on an earlier branch", async () => {
    const { channel, runtime, submit, mappings, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    mappings.set("5", { treeEntryId: "entry-5", branchId: "topic-2" });

    await dispatchCallback({ data: packCallbackData("yes", false), messageId: 5 });

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        metadata: expect.objectContaining({
          buttonValue: "yes",
          forcedBranchId: "topic-2",
          forcedTreeEntryId: "entry-5",
        }),
      }),
    );
  });

  it("carries no forced routing when tapping an unrecorded button message", async () => {
    const { channel, runtime, submit, dispatchCallback } = makeChannel();
    await channel.start(runtime);

    await dispatchCallback({ data: packCallbackData("yes", false), messageId: 5 });

    const submitted = submit.mock.calls[0]?.[0];
    expect(submitted.metadata.buttonValue).toBe("yes");
    expect(submitted.metadata.forcedBranchId).toBeUndefined();
  });
});

describe("error boundary", () => {
  it("logs grammy api errors", async () => {
    const { channel, runtime, triggerError } = makeChannel();
    await channel.start(runtime);

    const error = Object.create(GrammyError.prototype) as GrammyError;
    Object.assign(error, { error_code: 400, description: "bad request" });

    triggerError(error);

    expect(fakeLog.error).toHaveBeenCalledWith(
      expect.objectContaining({ errorCode: 400, description: "bad request" }),
      "telegram api rejected request",
    );
  });

  it("logs http errors", async () => {
    const { channel, runtime, triggerError } = makeChannel();
    await channel.start(runtime);

    const error = Object.create(HttpError.prototype) as HttpError;

    triggerError(error);

    expect(fakeLog.error).toHaveBeenCalledWith(expect.anything(), "could not reach telegram");
  });

  it("logs unknown errors", async () => {
    const { channel, runtime, triggerError } = makeChannel();
    await channel.start(runtime);

    triggerError(new Error("boom"));

    expect(fakeLog.error).toHaveBeenCalledWith(
      expect.anything(),
      "telegram update handling failed",
    );
  });
});

describe("respond error and result events", () => {
  it("sends a recoverable error notice", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([{ kind: "error", message: "transient", recoverable: true }]),
    });

    expect(calls).toContainEqual({ type: "send", text: "⚠️ Error: transient" });
  });

  it("sends an unrecoverable error notice with a follow-up hint", async () => {
    const { channel, runtime, calls } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([{ kind: "error", message: "fatal", recoverable: false }]),
    });

    const notice = calls.find((call) => call.text?.startsWith("⚠️ Error: fatal"));
    expect(notice?.text).toContain("needs your attention");
  });

  it("logs a result event that carries usage data", async () => {
    const { channel, runtime } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: textMessage("telegram", "hi"),
      events: stream([
        {
          kind: "result",
          stopReason: "done",
          sessionId: 100,
          result: { costUsd: 0.01, usage: { totalTokens: 42 } },
        } as unknown as AgentEvent,
      ]),
    });

    expect(fakeLog.info).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 100, costUsd: 0.01, tokens: 42 }),
      "exchange complete",
    );
  });
});

describe("status error handling", () => {
  it("swallows a typing action failure when no response is streaming", async () => {
    const { channel, runtime, api } = makeChannel();
    api.sendChatAction.mockRejectedValueOnce(new Error("nope"));
    await channel.start(runtime);

    channel.status("Gathering…");
    await settle();

    expect(api.sendChatAction).toHaveBeenCalled();
  });
});

describe("shutdownStatus error handling", () => {
  it("warns when the shutdown status update fails", async () => {
    const { channel, runtime, api } = makeChannel();
    api.sendMessage.mockRejectedValueOnce(new Error("send failed"));
    await channel.start(runtime);

    await channel.shutdownStatus("Wrapping up…");

    expect(fakeLog.warn).toHaveBeenCalledWith(expect.anything(), "shutdown status update failed");
  });
});

describe("message recording errors", () => {
  it("swallows a store.record failure", async () => {
    const { channel, runtime, store } = makeChannel({
      store: {
        record: vi.fn(() => {
          throw new Error("db down");
        }),
        resolve: vi.fn(() => null),
      },
    });
    void store;
    await channel.start(runtime);

    await expect(
      channel.respond({
        message: inboundWith("hi", 7),
        events: stream([
          { kind: "text", text: "Hi" },
          { kind: "result", stopReason: "done" },
        ]),
      }),
    ).resolves.toBeUndefined();
  });

  it("skips recording the inbound id when it is not numeric", async () => {
    const { channel, runtime, recorded } = makeChannel();
    await channel.start(runtime);

    await channel.respond({
      message: {
        text: "hi",
        channel: "telegram",
        receivedAt: new Date(),
        media: [],
        metadata: { messageId: "not-a-number" },
      } as never,
      events: stream([
        { kind: "text", text: "Hi" },
        { kind: "result", stopReason: "done" },
      ]),
    });

    expect(recorded).toEqual([
      {
        messageId: "1",
        routing: { treeEntryId: "entry-1", branchId: "topic-1" },
        direction: "outgoing",
      },
    ]);
  });
});

describe("media handling", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const documentMessage = (overrides: Record<string, unknown> = {}) => ({
    message_id: 5,
    document: { file_id: "doc-1", file_name: "report.pdf", file_size: 1024 },
    ...overrides,
  });

  it("ignores media when allowMedia is disabled", async () => {
    const { channel, runtime, submit, dispatchMessage } = makeChannel({ allowMedia: false });
    await channel.start(runtime);

    await dispatchMessage(documentMessage());

    expect(submit).not.toHaveBeenCalled();
  });

  it("ignores unresolvable media messages", async () => {
    const { channel, runtime, submit, dispatchMessage } = makeChannel({ allowMedia: true });
    await channel.start(runtime);

    await dispatchMessage({ message_id: 5, location: { latitude: 1, longitude: 2 } });

    expect(submit).not.toHaveBeenCalled();
  });

  it("downloads media and submits an attachment", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(4),
    })) as unknown as typeof fetch;

    const { channel, runtime, submit, dispatchMessage } = makeChannel({
      allowMedia: true,
      mediaDir: "/tmp/tachi-media-test",
    });
    await channel.start(runtime);

    await dispatchMessage(documentMessage());

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ media: expect.arrayContaining([expect.anything()]) }),
    );
  });

  it("fires typing before downloading an inbound media message", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(4),
    })) as unknown as typeof fetch;

    const { channel, runtime, api, submit, dispatchMessage } = makeChannel({
      allowMedia: true,
      mediaDir: "/tmp/tachi-media-test",
    });
    await channel.start(runtime);

    await dispatchMessage(documentMessage());

    expect(api.sendChatAction).toHaveBeenCalledWith(42, "typing");
    expect(submit).toHaveBeenCalled();
  });

  it("notifies with the size message when the file is too large", async () => {
    const { channel, runtime, submit, calls, dispatchMessage } = makeChannel({
      allowMedia: true,
    });
    await channel.start(runtime);

    await dispatchMessage(
      documentMessage({
        document: { file_id: "doc-1", file_name: "huge.bin", file_size: 21 * 1024 * 1024 },
      }),
    );

    expect(submit).not.toHaveBeenCalled();
    const notice = calls.find((call) => call.type === "send");
    expect(notice?.text).toContain("File too large");
  });

  it("sends a generic notice when the download fails", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      arrayBuffer: async () => new ArrayBuffer(0),
    })) as unknown as typeof fetch;

    const { channel, runtime, submit, calls, dispatchMessage } = makeChannel({
      allowMedia: true,
      mediaDir: "/tmp/tachi-media-test",
    });
    await channel.start(runtime);

    await dispatchMessage(documentMessage());

    expect(submit).not.toHaveBeenCalled();
    expect(calls).toContainEqual({
      type: "send",
      text: "Failed to download the file. Please try again.",
    });
  });

  it("swallows a failure while sending the media failure notice", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      arrayBuffer: async () => new ArrayBuffer(0),
    })) as unknown as typeof fetch;

    const { channel, runtime, api, dispatchMessage } = makeChannel({
      allowMedia: true,
      mediaDir: "/tmp/tachi-media-test",
    });
    api.sendMessage.mockRejectedValueOnce(new Error("send failed"));
    await channel.start(runtime);

    await dispatchMessage(documentMessage());

    expect(fakeLog.warn).toHaveBeenCalledWith(expect.anything(), "media failure notice failed");
  });
});

describe("forced routing with no reply target", () => {
  it("leaves the message untouched when there is no reply-to id", async () => {
    const { channel, runtime, submit, dispatchText } = makeChannel();
    await channel.start(runtime);

    await dispatchText("plain message");

    const submitted = submit.mock.calls[0]?.[0];
    expect(submitted.metadata.forcedBranchId).toBeUndefined();
  });
});

describe("deliver", () => {
  it("sends the delivery text and tracks the last outbound id", async () => {
    const { channel, runtime, calls } = makeChannel({ pushNotifications: false });
    await channel.start(runtime);

    await channel.deliver({ text: "scheduled reminder" });

    expect(calls).toContainEqual({ type: "send", text: rendered("scheduled reminder") });
    expect(channel.lastOutboundMessageId).toBe(1);
  });
});

describe("status while streaming with a failing renderer", () => {
  it("swallows a transient render failure", async () => {
    const { channel, runtime, api } = makeChannel();
    await channel.start(runtime);

    const { events, push, end } = pushableStream();
    const responding = channel.respond({ message: textMessage("telegram", "hi"), events });
    await settle();

    api.sendMessage.mockRejectedValueOnce(new Error("render failed"));
    channel.status("Pondering…");
    await settle();

    push({ kind: "text", text: "Answer" });
    end();
    await responding;
  });
});

describe("stop", () => {
  it("stops the bot when it is running", async () => {
    const { channel, runtime, setRunning, botStop } = makeChannel();
    await channel.start(runtime);
    setRunning(true);

    await channel.stop();

    expect(botStop).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the bot is not running", async () => {
    const { channel, runtime, botStop } = makeChannel();
    await channel.start(runtime);

    await channel.stop();

    expect(botStop).not.toHaveBeenCalled();
  });
});

describe("runtime guard", () => {
  it("throws when handling a message before start", async () => {
    const { channel } = makeChannel();

    await expect(
      channel.respond({
        message: textMessage("telegram", "hi"),
        events: stream([{ kind: "result", stopReason: "done" }]),
      }),
    ).rejects.toThrow("telegram channel not started");
  });
});

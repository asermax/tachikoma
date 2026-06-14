import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TELEGRAM_MAX_MESSAGE_LENGTH } from "../../src/extensions/telegram/chunking.ts";
import { EDIT_THROTTLE_MS, StreamRenderer } from "../../src/extensions/telegram/streaming.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

interface ApiCall {
  type: "send" | "edit" | "delete";
  messageId?: number;
  text?: string;
  parseMode?: string;
}

const fakeApi = () => {
  const calls: ApiCall[] = [];
  let next = 0;

  return {
    calls,
    sendMessage: vi.fn(async (_chat: number, text: string, other?: { parse_mode?: string }) => {
      calls.push({ type: "send", text, parseMode: other?.parse_mode });
      next += 1;
      return { message_id: next };
    }),
    editMessageText: vi.fn(
      async (_chat: number, messageId: number, text: string, other?: { parse_mode?: string }) => {
        calls.push({ type: "edit", messageId, text, parseMode: other?.parse_mode });
        return true;
      },
    ),
    deleteMessage: vi.fn(async (_chat: number, messageId: number) => {
      calls.push({ type: "delete", messageId });
      return true;
    }),
  };
};

const notModifiedError = () =>
  Object.assign(new Error("400: Bad Request: message is not modified"), {
    description: "Bad Request: message is not modified",
  });

describe("StreamRenderer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("holds an in-progress paragraph until a paragraph boundary closes it", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // No paragraph break yet — nothing renders during streaming.
    await renderer.appendText("Hello");
    expect(api.calls).toEqual([]);

    // Completing the paragraph reveals everything up to the break.
    await renderer.appendText(" there.\n\nSecond");
    expect(api.calls).toEqual([{ type: "send", text: "Hello there.", parseMode: "Markdown" }]);
  });

  it("renders complete paragraphs and throttles subsequent edits", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("First.\n\n");
    expect(api.calls).toEqual([{ type: "send", text: "First.", parseMode: "Markdown" }]);

    // Within the throttle window the new paragraph is buffered, not edited in.
    await renderer.appendText("Second.\n\n");
    expect(api.calls).toHaveLength(1);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Third.\n\n");
    expect(api.calls).toHaveLength(2);
    expect(api.calls[1]).toEqual({
      type: "edit",
      messageId: 1,
      text: "First.\n\nSecond.\n\nThird.",
      parseMode: "Markdown",
    });
  });

  it("bakes a tool marker between text segments and shows a live line while running", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // Settled text plus a live tool line renders the whole buffer beneath it,
    // even without a trailing paragraph break.
    await renderer.appendText("Let me search.");
    await renderer.appendTool("grep", { pattern: "skippable" });
    expect(api.calls.at(-1)).toEqual({
      type: "send",
      text: "Let me search.\n\n_🔧 Searching for 'skippable'_",
      parseMode: "Markdown",
    });

    // Text after the tool bakes the summary marker, separated by blank lines.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Found it.\n\n");
    expect(api.calls.at(-1)).toEqual({
      type: "edit",
      messageId: 1,
      text: "Let me search.\n\n_🔧 Searching for `skippable`_\n\nFound it.",
      parseMode: "Markdown",
    });
  });

  it("shows a marker-only message before any text arrives", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("Compacting…");

    expect(api.calls).toEqual([{ type: "send", text: "_Compacting…_", parseMode: "Markdown" }]);
  });

  it("finalize bypasses the throttle and flushes the trailing paragraph", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello");
    await renderer.appendText(" world");
    expect(api.calls).toEqual([]);

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)).toEqual({
      type: "send",
      text: "Hello world",
      parseMode: "Markdown",
    });
  });

  it("bakes a pending tool summary at finalize when no trailing text follows", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Done.");
    await renderer.appendTool("read", { path: "/tmp/notes/config.ts" });

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)).toEqual({
      type: "edit",
      messageId: 1,
      text: "Done.\n\n_🔧 Reading `config.ts`_",
      parseMode: "Markdown",
    });
  });

  it("finalizes the current message and continues in a new one past the edit cap", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    await renderer.appendText(`${first}\n\n`);
    expect(api.calls).toEqual([{ type: "send", text: first, parseMode: "Markdown" }]);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(`${second}\n\n`);

    expect(api.calls).toEqual([
      { type: "send", text: first, parseMode: "Markdown" },
      { type: "edit", messageId: 1, text: first, parseMode: "Markdown" },
      { type: "send", text: second, parseMode: "Markdown" },
    ]);
  });

  it("chunks overflow at finalize when the throttle held the last flushes back", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    const text = "a".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 100);

    await renderer.appendText("start");
    await renderer.appendText(text.slice(5));

    // "start" alone never rendered (no paragraph break), so the first overflow
    // chunk is sent fresh rather than edited into an existing message.
    expect(await renderer.finalize()).toBe(2);
    expect(api.calls).toEqual([
      {
        type: "send",
        text: `start${text.slice(5, TELEGRAM_MAX_MESSAGE_LENGTH)}`,
        parseMode: "Markdown",
      },
      { type: "send", text: text.slice(TELEGRAM_MAX_MESSAGE_LENGTH), parseMode: "Markdown" },
    ]);
  });

  it("treats 'message is not modified' rejections as benign", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValueOnce(notModifiedError());
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("again\n\n");

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("more\n\n");

    expect(api.calls.at(-1)).toMatchObject({ type: "edit", messageId: 1 });
  });

  it("falls back to final-send behavior when an edit fails", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValue(new Error("400: message can't be edited"));
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("world\n\n");

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("tail\n\n");
    expect(api.calls.filter((call) => call.type === "send")).toHaveLength(1);

    expect(await renderer.finalize()).toBe(2);
    expect(api.calls.at(-2)).toEqual({ type: "delete", messageId: 1 });
    expect(api.calls.at(-1)).toEqual({
      type: "send",
      text: "Hello\n\nworld\n\ntail",
      parseMode: "Markdown",
    });
  });

  it("deletes the placeholder when only markers were shown", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("Compacting…");

    expect(await renderer.finalize()).toBeNull();
    expect(api.calls.at(-1)).toEqual({ type: "delete", messageId: 1 });
  });

  it("returns null from finalize when nothing was rendered", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    expect(await renderer.finalize()).toBeNull();
    expect(api.calls).toEqual([]);
  });
});

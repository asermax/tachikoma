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

  it("sends the first text immediately and throttles subsequent edits", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello");
    expect(api.calls).toEqual([{ type: "send", text: "Hello", parseMode: "Markdown" }]);

    await renderer.appendText(" there");
    expect(api.calls).toHaveLength(1);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(", world");
    expect(api.calls).toHaveLength(2);
    expect(api.calls[1]).toEqual({
      type: "edit",
      messageId: 1,
      text: "Hello there, world",
      parseMode: "Markdown",
    });
  });

  it("skips edits when the content has not changed", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);

    await renderer.showTransient("⚙ read");
    expect(api.calls).toHaveLength(2);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.showTransient("⚙ read");
    expect(api.calls).toHaveLength(2);
  });

  it("renders tool markers as italic transient lines replaced by later text", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Working on it.");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);

    await renderer.showTransient("⚙ read");
    expect(api.calls.at(-1)).toMatchObject({
      type: "edit",
      text: "Working on it.\n\n_⚙ read_",
    });

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(" Done.");
    expect(api.calls.at(-1)).toMatchObject({
      type: "edit",
      text: "Working on it. Done.",
    });
  });

  it("shows a marker-only message before any text arrives", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("⚙ bash");

    expect(api.calls).toEqual([{ type: "send", text: "_⚙ bash_", parseMode: "Markdown" }]);
  });

  it("finalize bypasses the throttle and edits in the full remaining text", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello");
    await renderer.appendText(" world");

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)).toEqual({
      type: "edit",
      messageId: 1,
      text: "Hello world",
      parseMode: "Markdown",
    });
  });

  it("finalizes the current message and continues in a new one past the edit cap", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    await renderer.appendText(first);
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(`\n\n${second}`);

    expect(api.calls).toEqual([
      { type: "send", text: first, parseMode: "Markdown" },
      { type: "edit", messageId: 1, text: first, parseMode: "Markdown" },
      { type: "send", text: second, parseMode: "Markdown" },
    ]);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(" and more");

    expect(api.calls.at(-1)).toEqual({
      type: "edit",
      messageId: 2,
      text: `${second} and more`,
      parseMode: "Markdown",
    });
  });

  it("chunks overflow at finalize when the throttle held the last flushes back", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    const text = "a".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 100);

    await renderer.appendText("start");
    await renderer.appendText(text.slice(5));

    expect(await renderer.finalize()).toBe(2);
    expect(api.calls.slice(1)).toEqual([
      {
        type: "edit",
        messageId: 1,
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

    await renderer.appendText("Hello");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(" again");

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("!");

    expect(api.calls.at(-1)).toMatchObject({ type: "edit", messageId: 1 });
  });

  it("falls back to final-send behavior when an edit fails", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValue(new Error("400: message can't be edited"));
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(" world");

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("!");
    expect(api.calls.filter((call) => call.type === "send")).toHaveLength(1);

    expect(await renderer.finalize()).toBe(2);
    expect(api.calls.at(-2)).toEqual({ type: "delete", messageId: 1 });
    expect(api.calls.at(-1)).toEqual({
      type: "send",
      text: "Hello world!",
      parseMode: "Markdown",
    });
  });

  it("deletes the placeholder when only markers were shown", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("⚙ read");

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

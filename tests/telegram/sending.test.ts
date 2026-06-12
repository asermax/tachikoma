import { describe, expect, it, vi } from "vitest";
import {
  deliverText,
  isMarkdownParseError,
  notifyViaCopyDelete,
  sendChunked,
  sendWithMarkdownFallback,
} from "../../src/extensions/telegram/sending.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

const parseError = () =>
  Object.assign(new Error("400: Bad Request: can't parse entities"), {
    description: "Bad Request: can't parse entities: Can't find end of the entity",
  });

describe("isMarkdownParseError", () => {
  it("recognizes Telegram entity-parse rejections", () => {
    expect(isMarkdownParseError(parseError())).toBe(true);
  });

  it("rejects unrelated errors and non-errors", () => {
    expect(isMarkdownParseError(new Error("chat not found"))).toBe(false);
    expect(isMarkdownParseError(null)).toBe(false);
    expect(isMarkdownParseError("can't parse entities")).toBe(false);
  });
});

describe("sendWithMarkdownFallback", () => {
  it("sends with Markdown parse mode by default", async () => {
    const sendMessage = vi.fn().mockResolvedValue({ message_id: 7 });

    const id = await sendWithMarkdownFallback({ sendMessage }, 42, "*hi*");

    expect(id).toBe(7);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage).toHaveBeenCalledWith(42, "*hi*", { parse_mode: "Markdown" });
  });

  it("falls back to plain text on a parse error", async () => {
    const sendMessage = vi
      .fn()
      .mockImplementation(
        async (_chatId: number, _text: string, other?: { parse_mode?: string }) => {
          if (other?.parse_mode != null) throw parseError();
          return { message_id: 9 };
        },
      );

    const id = await sendWithMarkdownFallback({ sendMessage }, 42, "broken *markdown", {
      silent: true,
    });

    expect(id).toBe(9);
    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(sendMessage).toHaveBeenLastCalledWith(42, "broken *markdown", {
      disable_notification: true,
    });
  });

  it("propagates non-parse errors", async () => {
    const sendMessage = vi.fn().mockRejectedValue(new Error("chat not found"));

    await expect(sendWithMarkdownFallback({ sendMessage }, 42, "hi")).rejects.toThrow(
      "chat not found",
    );
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });
});

describe("sendChunked", () => {
  it("sends one message per chunk, in order", async () => {
    let next = 0;
    const sendMessage = vi.fn().mockImplementation(async () => {
      next += 1;
      return { message_id: next };
    });
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    const ids = await sendChunked({ sendMessage }, 42, `${first}\n\n${second}`, { silent: true });

    expect(ids).toEqual([1, 2]);
    expect(sendMessage).toHaveBeenNthCalledWith(1, 42, first, {
      disable_notification: true,
      parse_mode: "Markdown",
    });
    expect(sendMessage).toHaveBeenNthCalledWith(2, 42, second, {
      disable_notification: true,
      parse_mode: "Markdown",
    });
  });

  it("sends nothing for blank text", async () => {
    const sendMessage = vi.fn();

    expect(await sendChunked({ sendMessage }, 42, "  \n ")).toEqual([]);
    expect(sendMessage).not.toHaveBeenCalled();
  });
});

describe("notifyViaCopyDelete", () => {
  it("copies first, then deletes the original", async () => {
    const calls: string[] = [];
    const api = {
      copyMessage: vi.fn().mockImplementation(async () => {
        calls.push("copy");
        return { message_id: 20 };
      }),
      deleteMessage: vi.fn().mockImplementation(async () => {
        calls.push("delete");
        return true;
      }),
    };

    const id = await notifyViaCopyDelete(api, 42, 10, fakeLog, 0);

    expect(id).toBe(20);
    expect(calls).toEqual(["copy", "delete"]);
    expect(api.copyMessage).toHaveBeenCalledWith(42, 42, 10);
    expect(api.deleteMessage).toHaveBeenCalledWith(42, 10);
  });

  it("skips the delete when the copy fails", async () => {
    const api = {
      copyMessage: vi.fn().mockRejectedValue(new Error("copy failed")),
      deleteMessage: vi.fn(),
    };

    expect(await notifyViaCopyDelete(api, 42, 10, fakeLog, 0)).toBeNull();
    expect(api.deleteMessage).not.toHaveBeenCalled();
  });

  it("retries the delete and accepts the duplicate after exhausting attempts", async () => {
    const api = {
      copyMessage: vi.fn().mockResolvedValue({ message_id: 20 }),
      deleteMessage: vi.fn().mockRejectedValue(new Error("delete failed")),
    };

    expect(await notifyViaCopyDelete(api, 42, 10, fakeLog, 0)).toBe(20);
    expect(api.deleteMessage).toHaveBeenCalledTimes(3);
  });
});

describe("deliverText", () => {
  const fakeApi = () => {
    const calls: string[] = [];
    let next = 0;

    return {
      calls,
      api: {
        sendMessage: vi.fn().mockImplementation(async (_chat: number, text: string) => {
          calls.push(`send:${text.slice(0, 1)}`);
          next += 1;
          return { message_id: next };
        }),
        sendChatAction: vi.fn(),
        copyMessage: vi.fn().mockImplementation(async () => {
          calls.push("copy");
          return { message_id: 100 + next };
        }),
        deleteMessage: vi.fn().mockImplementation(async () => {
          calls.push("delete");
          return true;
        }),
      },
    };
  };

  it("sends silently and fires the push via copy+delete", async () => {
    const { api, calls } = fakeApi();

    const id = await deliverText(api, 42, "notice", true, fakeLog, 0);

    expect(id).toBe(101);
    expect(calls).toEqual(["send:n", "copy", "delete"]);
    expect(api.sendMessage).toHaveBeenCalledWith(42, "notice", {
      disable_notification: true,
      parse_mode: "Markdown",
    });
  });

  it("sends audibly without copy+delete when push notifications are off", async () => {
    const { api, calls } = fakeApi();

    const id = await deliverText(api, 42, "notice", false, fakeLog, 0);

    expect(id).toBe(1);
    expect(calls).toEqual(["send:n"]);
    expect(api.sendMessage).toHaveBeenCalledWith(42, "notice", { parse_mode: "Markdown" });
  });
});

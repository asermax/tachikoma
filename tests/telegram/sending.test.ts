import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type TelegramPayload,
  toTelegramEntities,
} from "../../src/extensions/telegram/entities.ts";
import {
  deliverText,
  editWithFallback,
  forceNotification,
  isEntitiesTooManyError,
  isMarkdownParseError,
  isMessageNotModifiedError,
  isMessageTooLongError,
  sendChunked,
  sendWithFallback,
  startTyping,
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

const notModifiedError = () =>
  Object.assign(new Error("400: Bad Request: message is not modified"), {
    description: "Bad Request: message is not modified",
  });

const tooLongError = () =>
  Object.assign(new Error("400: Bad Request: message is too long"), {
    description: "Bad Request: message is too long",
  });

const tooManyEntitiesError = () =>
  Object.assign(new Error("400: Bad Request: entities too many"), {
    description: "Bad Request: entities too many",
  });

/** A sendMessage/editMessageText mock that rejects the formatted (entity-bearing) call. */
const rejectFormatted =
  (error: Error) =>
  async (...args: unknown[]) => {
    const other = args.at(-1) as { entities?: unknown[] } | string | undefined;
    if (other != null && typeof other === "object" && other.entities != null) throw error;
    return { message_id: 9 };
  };

describe("isMarkdownParseError", () => {
  it("recognizes Telegram entity-parse rejections", () => {
    expect(isMarkdownParseError(parseError())).toBe(true);
  });

  it("rejects unrelated errors and non-errors", () => {
    expect(isMarkdownParseError(new Error("chat not found"))).toBe(false);
    expect(isMarkdownParseError(null)).toBe(false);
    expect(isMarkdownParseError("can't parse entities")).toBe(false);
  });

  it("uses the Error message when there is no description string", () => {
    expect(isMarkdownParseError(new Error("can't parse entities here"))).toBe(true);
  });

  it("ignores a non-string description", () => {
    expect(isMarkdownParseError({ description: 42 })).toBe(false);
  });
});

describe("isEntitiesTooManyError", () => {
  it("recognizes entities-too-many rejections", () => {
    expect(isEntitiesTooManyError(tooManyEntitiesError())).toBe(true);
  });

  it("rejects unrelated errors", () => {
    expect(isEntitiesTooManyError(new Error("chat not found"))).toBe(false);
  });
});

describe("isMessageTooLongError", () => {
  it("recognizes too-long rejections", () => {
    expect(isMessageTooLongError(tooLongError())).toBe(true);
  });

  it("rejects unrelated errors", () => {
    expect(isMessageTooLongError(new Error("chat not found"))).toBe(false);
  });
});

describe("isMessageNotModifiedError", () => {
  it("recognizes not-modified rejections", () => {
    expect(isMessageNotModifiedError(notModifiedError())).toBe(true);
  });

  it("rejects unrelated errors", () => {
    expect(isMessageNotModifiedError(new Error("chat not found"))).toBe(false);
  });
});

describe("editWithFallback", () => {
  it("edits with the converted entity payload", async () => {
    const editMessageText = vi.fn().mockResolvedValue(true);
    const payload = toTelegramEntities("**hi**");

    await editWithFallback({ editMessageText }, 42, 7, payload);

    expect(editMessageText).toHaveBeenCalledTimes(1);
    expect(editMessageText).toHaveBeenCalledWith(42, 7, payload.text, {
      entities: payload.entities,
    });
  });

  it("swallows a not-modified rejection without retrying as plain text", async () => {
    const editMessageText = vi.fn().mockRejectedValue(notModifiedError());

    await expect(
      editWithFallback({ editMessageText }, 42, 7, toTelegramEntities("same")),
    ).resolves.toBeUndefined();
    expect(editMessageText).toHaveBeenCalledTimes(1);
  });

  it("propagates non-render errors", async () => {
    const editMessageText = vi.fn().mockRejectedValue(new Error("chat not found"));

    await expect(
      editWithFallback({ editMessageText }, 42, 7, toTelegramEntities("x")),
    ).rejects.toThrow("chat not found");
    expect(editMessageText).toHaveBeenCalledTimes(1);
  });

  it("falls back to plain text on a parse rejection", async () => {
    const editMessageText = vi.fn().mockImplementation(rejectFormatted(parseError()));
    const payload = toTelegramEntities("broken *markdown");

    await editWithFallback({ editMessageText }, 42, 7, payload);

    expect(editMessageText).toHaveBeenCalledTimes(2);
    expect(editMessageText).toHaveBeenLastCalledWith(42, 7, payload.text);
  });

  it("falls back to plain text on an entities-too-many rejection", async () => {
    const editMessageText = vi.fn().mockImplementation(rejectFormatted(tooManyEntitiesError()));
    const payload = toTelegramEntities("**hi**");

    await editWithFallback({ editMessageText }, 42, 7, payload);

    expect(editMessageText).toHaveBeenCalledTimes(2);
    expect(editMessageText).toHaveBeenLastCalledWith(42, 7, payload.text);
  });

  it("swallows a not-modified rejection on the plain-text fallback", async () => {
    const editMessageText = vi
      .fn()
      .mockImplementationOnce(async () => {
        throw parseError();
      })
      .mockImplementationOnce(async () => {
        throw notModifiedError();
      });

    await expect(
      editWithFallback({ editMessageText }, 42, 7, toTelegramEntities("broken *markdown")),
    ).resolves.toBeUndefined();
    expect(editMessageText).toHaveBeenCalledTimes(2);
  });

  it("propagates a non-not-modified failure from the plain-text fallback", async () => {
    const editMessageText = vi
      .fn()
      .mockImplementationOnce(async () => {
        throw parseError();
      })
      .mockImplementationOnce(async () => {
        throw new Error("chat not found");
      });

    await expect(
      editWithFallback({ editMessageText }, 42, 7, toTelegramEntities("broken *markdown")),
    ).rejects.toThrow("chat not found");
    expect(editMessageText).toHaveBeenCalledTimes(2);
  });
});

describe("startTyping", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends an immediate typing action and refreshes on an interval until stopped", () => {
    const sendChatAction = vi.fn().mockResolvedValue(true);

    const stop = startTyping({ sendChatAction }, 42, fakeLog);

    expect(sendChatAction).toHaveBeenCalledTimes(1);
    expect(sendChatAction).toHaveBeenCalledWith(42, "typing");

    vi.advanceTimersByTime(5000);
    expect(sendChatAction).toHaveBeenCalledTimes(2);

    stop();
    vi.advanceTimersByTime(10000);
    expect(sendChatAction).toHaveBeenCalledTimes(2);
  });

  it("logs when the typing action fails", async () => {
    const log = {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    } as unknown as Logger;
    const sendChatAction = vi.fn().mockRejectedValue(new Error("flood"));

    const stop = startTyping({ sendChatAction }, 42, log);
    await vi.runOnlyPendingTimersAsync();

    expect(log.debug).toHaveBeenCalledWith({ err: expect.any(Error) }, "typing chat action failed");

    stop();
  });
});

describe("sendWithFallback", () => {
  it("sends the converted entity payload by default", async () => {
    const sendMessage = vi.fn().mockResolvedValue({ message_id: 7 });
    const payload = toTelegramEntities("**hi**");

    const id = await sendWithFallback({ sendMessage }, 42, payload);

    expect(id).toBe(7);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage).toHaveBeenCalledWith(42, payload.text, { entities: payload.entities });
  });

  it("falls back to plain text when the formatted send is too long", async () => {
    const sendMessage = vi.fn().mockImplementation(rejectFormatted(tooLongError()));
    const payload = toTelegramEntities("long raw text");

    const id = await sendWithFallback({ sendMessage }, 42, payload);

    expect(id).toBe(9);
    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(sendMessage).toHaveBeenLastCalledWith(42, payload.text, {});
  });

  it("falls back to plain text on a parse error", async () => {
    const sendMessage = vi.fn().mockImplementation(rejectFormatted(parseError()));
    const payload = toTelegramEntities("broken *markdown");

    const id = await sendWithFallback({ sendMessage }, 42, payload, { silent: true });

    expect(id).toBe(9);
    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(sendMessage).toHaveBeenLastCalledWith(42, payload.text, { disable_notification: true });
  });

  it("propagates non-render errors", async () => {
    const sendMessage = vi.fn().mockRejectedValue(new Error("chat not found"));

    await expect(sendWithFallback({ sendMessage }, 42, toTelegramEntities("hi"))).rejects.toThrow(
      "chat not found",
    );
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });
});

describe("sendChunked", () => {
  it("sends one message per chunk, in order, each carrying its entities", async () => {
    let next = 0;
    const sendMessage = vi.fn().mockImplementation(async () => {
      next += 1;
      return { message_id: next };
    });
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    const ids = await sendChunked({ sendMessage }, 42, `${first}\n\n${second}`, { silent: true });

    expect(ids).toEqual([1, 2]);
    const firstPayload: TelegramPayload = toTelegramEntities(first);
    const secondPayload: TelegramPayload = toTelegramEntities(second);
    expect(sendMessage).toHaveBeenNthCalledWith(1, 42, firstPayload.text, {
      disable_notification: true,
      entities: firstPayload.entities,
    });
    expect(sendMessage).toHaveBeenNthCalledWith(2, 42, secondPayload.text, {
      disable_notification: true,
      entities: secondPayload.entities,
    });
  });

  it("sends nothing for blank text", async () => {
    const sendMessage = vi.fn();

    expect(await sendChunked({ sendMessage }, 42, "  \n ")).toEqual([]);
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("silences every chunk but the last when notifyOnlyLast is set", async () => {
    let next = 0;
    const sendMessage = vi.fn().mockImplementation(async () => {
      next += 1;
      return { message_id: next };
    });
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    const ids = await sendChunked({ sendMessage }, 42, `${first}\n\n${second}`, {
      notifyOnlyLast: true,
    });

    expect(ids).toEqual([1, 2]);
    const firstPayload: TelegramPayload = toTelegramEntities(first);
    const secondPayload: TelegramPayload = toTelegramEntities(second);
    expect(sendMessage).toHaveBeenNthCalledWith(1, 42, firstPayload.text, {
      disable_notification: true,
      entities: firstPayload.entities,
    });
    expect(sendMessage).toHaveBeenNthCalledWith(2, 42, secondPayload.text, {
      entities: secondPayload.entities,
    });
  });

  it("keeps a bold span whole in a single chunk instead of splitting it", async () => {
    const sendMessage = vi.fn().mockResolvedValue({ message_id: 1 });
    // A bold span straddling the 4096 boundary: the chunker moves it whole into
    // one message, so only one chunk carries the bold entity.
    const markdown = `${"x".repeat(4090)}**${"y".repeat(20)}**`;

    await sendChunked({ sendMessage }, 42, markdown);

    const boldChunks = sendMessage.mock.calls.filter(([, , other]) =>
      (other as { entities?: { type: string }[] })?.entities?.some((e) => e.type === "bold"),
    );
    expect(boldChunks).toHaveLength(1);
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
        deleteMessage: vi.fn(),
      },
    };
  };

  it("fires one push on the last chunk, silencing the rest", async () => {
    const { api, calls } = fakeApi();
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);
    const firstPayload: TelegramPayload = toTelegramEntities(first);
    const secondPayload: TelegramPayload = toTelegramEntities(second);

    const id = await deliverText(api, 42, `${first}\n\n${second}`, true);

    expect(id).toBe(2);
    expect(calls).toEqual(["send:a", "send:b"]);
    expect(api.sendMessage).toHaveBeenNthCalledWith(1, 42, firstPayload.text, {
      disable_notification: true,
      entities: firstPayload.entities,
    });
    expect(api.sendMessage).toHaveBeenNthCalledWith(2, 42, secondPayload.text, {
      entities: secondPayload.entities,
    });
  });

  it("sends audibly when push notifications are off", async () => {
    const { api, calls } = fakeApi();
    const payload: TelegramPayload = toTelegramEntities("notice");

    const id = await deliverText(api, 42, "notice", false);

    expect(id).toBe(1);
    expect(calls).toEqual(["send:n"]);
    expect(api.sendMessage).toHaveBeenCalledWith(42, payload.text, { entities: payload.entities });
  });

  it("returns null when there is nothing to send", async () => {
    const { api } = fakeApi();

    expect(await deliverText(api, 42, "   ", true)).toBeNull();
    expect(api.sendMessage).not.toHaveBeenCalled();
  });
});

describe("forceNotification", () => {
  it("copies the message within the same chat and deletes the original", async () => {
    const copyMessage = vi.fn().mockResolvedValue({ message_id: 9 });
    const deleteMessage = vi.fn().mockResolvedValue(true);

    const id = await forceNotification({ copyMessage, deleteMessage }, 42, 7, fakeLog);

    expect(id).toBe(9);
    expect(copyMessage).toHaveBeenCalledWith(42, 42, 7);
    expect(deleteMessage).toHaveBeenCalledWith(42, 7);
  });

  it("returns the copy id even when the original delete fails", async () => {
    const copyMessage = vi.fn().mockResolvedValue({ message_id: 9 });
    const deleteMessage = vi.fn().mockRejectedValue(new Error("already gone"));

    const id = await forceNotification({ copyMessage, deleteMessage }, 42, 7, fakeLog);

    expect(id).toBe(9);
  });

  it("propagates a copy failure without deleting (leaves the original in place)", async () => {
    const copyMessage = vi.fn().mockRejectedValue(new Error("chat not found"));
    const deleteMessage = vi.fn();

    await expect(forceNotification({ copyMessage, deleteMessage }, 42, 7, fakeLog)).rejects.toThrow(
      "chat not found",
    );
    expect(deleteMessage).not.toHaveBeenCalled();
  });
});

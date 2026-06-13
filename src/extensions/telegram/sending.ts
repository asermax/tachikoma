import type { Logger } from "../../log.ts";
import { splitMessage } from "./chunking.ts";

// Telegram keeps a chat action visible for ~5 seconds, so refresh just before it expires.
const TYPING_REFRESH_MS = 5000;

const DELETE_RETRY_ATTEMPTS = 3;

export interface SendMessageOptions {
  parse_mode?: "Markdown";
  disable_notification?: boolean;
}

export interface EditMessageOptions {
  parse_mode?: "Markdown";
}

/** Narrow grammY API surface the channel sends through — fakeable in tests. */
export interface SendApi {
  sendMessage(
    chatId: number,
    text: string,
    other?: SendMessageOptions,
  ): Promise<{ message_id: number }>;
  sendChatAction(chatId: number, action: "typing"): Promise<unknown>;
  copyMessage(
    chatId: number,
    fromChatId: number,
    messageId: number,
  ): Promise<{ message_id: number }>;
  deleteMessage(chatId: number, messageId: number): Promise<unknown>;
  editMessageText(
    chatId: number,
    messageId: number,
    text: string,
    other?: EditMessageOptions,
  ): Promise<unknown>;
}

const errorDetail = (error: unknown): string => {
  if (typeof error !== "object" || error == null) return "";

  const description = (error as { description?: unknown }).description;

  return typeof description === "string"
    ? description
    : error instanceof Error
      ? error.message
      : "";
};

export const isMarkdownParseError = (error: unknown): boolean =>
  /can't parse entities/i.test(errorDetail(error));

/** Telegram rejects edits whose content matches the current message — benign. */
export const isMessageNotModifiedError = (error: unknown): boolean =>
  /message is not modified/i.test(errorDetail(error));

/** Try Markdown first; on a Telegram entity-parse rejection resend as plain text. */
export const sendWithMarkdownFallback = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  text: string,
  options: { silent?: boolean } = {},
): Promise<number> => {
  const base: SendMessageOptions = options.silent === true ? { disable_notification: true } : {};

  try {
    const sent = await api.sendMessage(chatId, text, { ...base, parse_mode: "Markdown" });
    return sent.message_id;
  } catch (error) {
    if (!isMarkdownParseError(error)) throw error;

    const sent = await api.sendMessage(chatId, text, base);
    return sent.message_id;
  }
};

/**
 * Edit a message trying Markdown first, falling back to plain text on a parse
 * rejection. "Message is not modified" rejections are swallowed — the visible
 * content already matches.
 */
export const editWithMarkdownFallback = async (
  api: Pick<SendApi, "editMessageText">,
  chatId: number,
  messageId: number,
  text: string,
): Promise<void> => {
  try {
    await api.editMessageText(chatId, messageId, text, { parse_mode: "Markdown" });
  } catch (error) {
    if (isMessageNotModifiedError(error)) return;
    if (!isMarkdownParseError(error)) throw error;

    try {
      await api.editMessageText(chatId, messageId, text);
    } catch (fallbackError) {
      if (!isMessageNotModifiedError(fallbackError)) throw fallbackError;
    }
  }
};

/** Send text as one or more messages, split at Telegram's length limit. */
export const sendChunked = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  text: string,
  options: { silent?: boolean } = {},
): Promise<number[]> => {
  if (text.trim().length === 0) return [];

  const ids: number[] = [];

  for (const chunk of splitMessage(text)) {
    ids.push(await sendWithMarkdownFallback(api, chatId, chunk, options));
  }

  return ids;
};

/**
 * Copy+delete a silently-sent message so the copy fires a push notification.
 * Copy-first ordering keeps the
 * text safe: on copy failure the original is preserved and no delete runs; on
 * delete failure the duplicate is accepted after retries.
 */
export const notifyViaCopyDelete = async (
  api: Pick<SendApi, "copyMessage" | "deleteMessage">,
  chatId: number,
  messageId: number,
  log: Logger,
  retryDelayMs = 500,
): Promise<number | null> => {
  let copied: { message_id: number };

  try {
    copied = await api.copyMessage(chatId, chatId, messageId);
  } catch (error) {
    log.warn({ err: error, messageId }, "copy for push notification failed — keeping original");
    return null;
  }

  for (let attempt = 0; attempt < DELETE_RETRY_ATTEMPTS; attempt += 1) {
    try {
      await api.deleteMessage(chatId, messageId);
      return copied.message_id;
    } catch (error) {
      if (attempt < DELETE_RETRY_ATTEMPTS - 1) {
        await sleep(retryDelayMs);
      } else {
        log.warn({ err: error, messageId }, "delete after copy failed — duplicate left visible");
      }
    }
  }

  return copied.message_id;
};

/** Full background-delivery flow: silent chunked send, then copy+delete to fire the push. */
export const deliverText = async (
  api: SendApi,
  chatId: number,
  text: string,
  pushNotifications: boolean,
  log: Logger,
  retryDelayMs = 500,
): Promise<number | null> => {
  const ids = await sendChunked(api, chatId, text, { silent: pushNotifications });
  const lastId = ids.at(-1);

  if (lastId == null) return null;
  if (!pushNotifications) return lastId;

  return (await notifyViaCopyDelete(api, chatId, lastId, log, retryDelayMs)) ?? lastId;
};

/** Show a typing indicator until the returned stop function is called. */
export const startTyping = (
  api: Pick<SendApi, "sendChatAction">,
  chatId: number,
  log: Logger,
): (() => void) => {
  const send = () => {
    api
      .sendChatAction(chatId, "typing")
      .catch((error) => log.debug({ err: error }, "typing chat action failed"));
  };

  send();
  const timer = setInterval(send, TYPING_REFRESH_MS);
  timer.unref();

  return () => clearInterval(timer);
};

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

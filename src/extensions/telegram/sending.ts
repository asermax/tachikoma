import type { Logger } from "../../log.ts";
import { splitMessage } from "./chunking.ts";
import { toTelegramMarkdown } from "./markdown.ts";

// Telegram keeps a chat action visible for ~5 seconds, so refresh just before it expires.
const TYPING_REFRESH_MS = 5000;

export interface SendMessageOptions {
  parse_mode?: "MarkdownV2";
  disable_notification?: boolean;
}

export interface EditMessageOptions {
  parse_mode?: "MarkdownV2";
}

/** Narrow grammY API surface the channel sends through — fakeable in tests. */
export interface SendApi {
  sendMessage(
    chatId: number,
    text: string,
    other?: SendMessageOptions,
  ): Promise<{ message_id: number }>;
  sendChatAction(chatId: number, action: "typing"): Promise<unknown>;
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

/**
 * MarkdownV2 escaping inflates length, so a chunk that fit as raw text can
 * overflow once converted — Telegram then rejects it as too long. Treated like
 * a parse rejection: resend the raw text, which is always within the limit.
 */
export const isMessageTooLongError = (error: unknown): boolean =>
  /message is too long/i.test(errorDetail(error));

/** Either failure mode of the converted MarkdownV2 send — both recover by resending raw. */
const isMarkdownRenderError = (error: unknown): boolean =>
  isMarkdownParseError(error) || isMessageTooLongError(error);

/** Telegram rejects edits whose content matches the current message — benign. */
export const isMessageNotModifiedError = (error: unknown): boolean =>
  /message is not modified/i.test(errorDetail(error));

/**
 * Send `text` (GitHub-flavored markdown) converted to MarkdownV2; on a Telegram
 * render rejection resend the raw text as plain, so formatting is best-effort
 * but the message is never lost.
 */
export const sendWithMarkdownFallback = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  text: string,
  options: { silent?: boolean } = {},
): Promise<number> => {
  const base: SendMessageOptions = options.silent === true ? { disable_notification: true } : {};

  try {
    const sent = await api.sendMessage(chatId, toTelegramMarkdown(text), {
      ...base,
      parse_mode: "MarkdownV2",
    });
    return sent.message_id;
  } catch (error) {
    if (!isMarkdownRenderError(error)) throw error;

    const sent = await api.sendMessage(chatId, text, base);
    return sent.message_id;
  }
};

/**
 * Edit a message with `text` converted to MarkdownV2, falling back to the raw
 * text on a render rejection. "Message is not modified" rejections are swallowed
 * — the visible content already matches.
 */
export const editWithMarkdownFallback = async (
  api: Pick<SendApi, "editMessageText">,
  chatId: number,
  messageId: number,
  text: string,
): Promise<void> => {
  try {
    await api.editMessageText(chatId, messageId, toTelegramMarkdown(text), {
      parse_mode: "MarkdownV2",
    });
  } catch (error) {
    if (isMessageNotModifiedError(error)) return;
    if (!isMarkdownRenderError(error)) throw error;

    try {
      await api.editMessageText(chatId, messageId, text);
    } catch (fallbackError) {
      if (!isMessageNotModifiedError(fallbackError)) throw fallbackError;
    }
  }
};

/**
 * Send text as one or more messages, split at Telegram's length limit. With
 * `notifyOnlyLast`, every chunk but the last is sent silently so the delivery
 * fires exactly one push notification — on the final chunk.
 */
export const sendChunked = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  text: string,
  options: { silent?: boolean; notifyOnlyLast?: boolean } = {},
): Promise<number[]> => {
  if (text.trim().length === 0) return [];

  const chunks = splitMessage(text);
  const ids: number[] = [];

  for (const [index, chunk] of chunks.entries()) {
    const silent =
      options.notifyOnlyLast === true ? index < chunks.length - 1 : options.silent === true;

    ids.push(await sendWithMarkdownFallback(api, chatId, chunk, { silent }));
  }

  return ids;
};

/**
 * Background-delivery flow: a chunked send that, when push notifications are on,
 * silences every chunk but the last so the user gets exactly one push — on the
 * final chunk. Editing never notifies and Telegram has no notify-in-place API,
 * so a fresh loud send is the only way to fire a push; sending the last chunk
 * loud does that directly, with a stable message id and no extra round-trips.
 */
export const deliverText = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  text: string,
  pushNotifications: boolean,
): Promise<number | null> => {
  const ids = await sendChunked(api, chatId, text, { notifyOnlyLast: pushNotifications });

  return ids.at(-1) ?? null;
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

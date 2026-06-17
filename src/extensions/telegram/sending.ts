import type { MessageEntity } from "grammy/types";
import type { Logger } from "../../log.ts";
import { splitMessageWithEntities, type TelegramPayload, toTelegramEntities } from "./entities.ts";

// Telegram keeps a chat action visible for ~5 seconds, so refresh just before it expires.
const TYPING_REFRESH_MS = 5000;

export interface SendMessageOptions {
  entities?: MessageEntity[];
  disable_notification?: boolean;
}

export interface EditMessageOptions {
  entities?: MessageEntity[];
}

/** Narrow grammY API surface the channel sends through — fakeable in tests. */
export interface SendApi {
  sendMessage(
    chatId: number,
    text: string,
    other?: SendMessageOptions,
  ): Promise<{ message_id: number }>;
  copyMessage(
    chatId: number,
    fromChatId: number,
    messageId: number,
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

/** Telegram rejects an invalid/overlapping entity set with "can't parse entities". */
export const isMarkdownParseError = (error: unknown): boolean =>
  /can't parse entities/i.test(errorDetail(error));

/**
 * A formatting-dense message can exceed Telegram's per-message entity cap and be
 * rejected as "entities too many". Treated like any other render rejection: resend
 * the raw text, which carries no entities.
 */
export const isEntitiesTooManyError = (error: unknown): boolean =>
  /entities too many/i.test(errorDetail(error));

/**
 * Edge cases can still overflow the 4096-char text limit (e.g. a single oversize
 * entity dropped into a near-full chunk). Resending the raw text recovers.
 */
export const isMessageTooLongError = (error: unknown): boolean =>
  /message is too long/i.test(errorDetail(error));

/** Any rejection caused by the rendered payload — all recover by resending raw text. */
const isRenderError = (error: unknown): boolean =>
  isMarkdownParseError(error) || isEntitiesTooManyError(error) || isMessageTooLongError(error);

/** Telegram rejects edits whose content matches the current message — benign. */
export const isMessageNotModifiedError = (error: unknown): boolean =>
  /message is not modified/i.test(errorDetail(error));

/**
 * Send a converted entity payload via `send`; on a Telegram render rejection
 * resend the payload's text plain (no entities), so formatting is best-effort
 * but the message is never lost. With `parse_mode` omitted, Telegram treats the
 * text as literal — ordinary punctuation (`.`, `-`, `!`) can't trigger a parse
 * failure, so the fallback is rare. `send` is bound by each caller to its own API
 * surface and options (the channel's `SendApi` + `disable_notification`, or a
 * tool's `ToolApi` + `reply_markup`), so the convert-then-fallback policy lives
 * in one place without coupling the two. `send` gets `other` plus `entities` on
 * the converted attempt and `other` alone on the raw fallback.
 */
export const sendEntitiesOrFallback = async <O>(
  send: (text: string, other: O) => Promise<{ message_id: number }>,
  payload: TelegramPayload,
  other: O,
  log?: Logger,
): Promise<number> => {
  try {
    const sent = await send(payload.text, { ...other, entities: payload.entities } as O);
    return sent.message_id;
  } catch (error) {
    if (!isRenderError(error)) throw error;

    log?.debug({ err: error }, "telegram render rejected; resent raw text");

    const sent = await send(payload.text, other);
    return sent.message_id;
  }
};

/**
 * Send a converted entity payload; on a Telegram render rejection resend the text
 * plain (no entities), so formatting is best-effort but the message is never lost.
 * With `parse_mode` omitted, Telegram treats the text as literal — ordinary
 * punctuation (`.`, `-`, `!`) can't trigger a parse failure, so the fallback is rare.
 */
export const sendWithFallback = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  payload: TelegramPayload,
  options: { silent?: boolean } = {},
): Promise<number> => {
  const base: SendMessageOptions = options.silent === true ? { disable_notification: true } : {};
  return sendEntitiesOrFallback(
    (text, other) => api.sendMessage(chatId, text, other),
    payload,
    base,
  );
};

/**
 * Edit a message with a converted entity payload, falling back to plain text on a
 * render rejection. "Message is not modified" rejections are swallowed — the
 * visible content already matches.
 */
export const editWithFallback = async (
  api: Pick<SendApi, "editMessageText">,
  chatId: number,
  messageId: number,
  payload: TelegramPayload,
  log?: Logger,
): Promise<void> => {
  try {
    await api.editMessageText(chatId, messageId, payload.text, { entities: payload.entities });
  } catch (error) {
    if (isMessageNotModifiedError(error)) return;
    if (!isRenderError(error)) throw error;

    log?.debug({ err: error, messageId }, "telegram edit render rejected; resent raw text");

    try {
      await api.editMessageText(chatId, messageId, payload.text);
    } catch (fallbackError) {
      if (!isMessageNotModifiedError(fallbackError)) throw fallbackError;
    }
  }
};

/** Convert GFM markdown to an entity payload, then split it entity-safely at the length limit. */
export const convertAndSplit = (text: string): TelegramPayload[] => {
  const { text: body, entities } = toTelegramEntities(text);
  return splitMessageWithEntities(body, entities);
};

/**
 * Send text as one or more messages, split at Telegram's length limit without ever
 * cutting a formatting entity across two messages. With `notifyOnlyLast`, every
 * chunk but the last is sent silently so the delivery fires exactly one push
 * notification — on the final chunk.
 */
export const sendChunked = async (
  api: Pick<SendApi, "sendMessage">,
  chatId: number,
  text: string,
  options: { silent?: boolean; notifyOnlyLast?: boolean } = {},
): Promise<number[]> => {
  if (text.trim().length === 0) return [];

  const chunks = convertAndSplit(text);
  const ids: number[] = [];

  for (const [index, chunk] of chunks.entries()) {
    const silent =
      options.notifyOnlyLast === true ? index < chunks.length - 1 : options.silent === true;

    ids.push(await sendWithFallback(api, chatId, chunk, { silent }));
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

/**
 * Force a push notification for an already-delivered message — the streaming
 * renderer edits its message in place as text arrives, and Telegram edits never
 * notify (there is no notify-in-place API). Copying the message within the same
 * chat creates a fresh message that notifies; deleting the original leaves a
 * single loud message in its place. The delete is best-effort: a failure leaves a
 * silent duplicate rather than throwing the delivery away.
 */
export const forceNotification = async (
  api: Pick<SendApi, "copyMessage" | "deleteMessage">,
  chatId: number,
  messageId: number,
  log: Logger,
): Promise<number> => {
  const copied = await api.copyMessage(chatId, chatId, messageId);

  await api
    .deleteMessage(chatId, messageId)
    .catch((error) =>
      log.debug({ err: error, messageId }, "force-notification delete failed; duplicate left"),
    );

  return copied.message_id;
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

import type { Message, MessageReactionUpdated, ReactionType } from "grammy/types";

import type { InboundMessage, MediaAttachment } from "../../domain/message.ts";

export const CHANNEL_NAME = "telegram";

const REPLY_QUOTE_MAX = 280;

/** Keep a reply/reaction quote short: head and tail with an ellipsis between. */
const truncateQuote = (text: string): string => {
  const stripped = text.trim();

  if (stripped.length <= REPLY_QUOTE_MAX) return stripped;

  const half = Math.floor((REPLY_QUOTE_MAX - 1) / 2);

  return `${stripped.slice(0, half)}…${stripped.slice(-half)}`;
};

/** Quote of a replied-to message, prepended to the agent prompt as context. */
export const replyQuote = (message: Pick<Message, "reply_to_message">): string | null => {
  const replied = message.reply_to_message;
  if (replied == null) return null;

  const text = replied.text ?? replied.caption;
  if (text == null || text.trim().length === 0) return null;

  return `Replied to:\n> ${truncateQuote(text)}`;
};

/** The id of the bot/user message a reply targets, as a string key. */
export const replyTargetId = (message: Pick<Message, "reply_to_message">): string | null =>
  message.reply_to_message != null ? String(message.reply_to_message.message_id) : null;

export const mapTextMessage = (
  message: Pick<Message, "text" | "message_id" | "reply_to_message">,
  options?: { skipQuote?: boolean },
): InboundMessage | null => {
  const text = message.text?.trim() ?? "";

  if (text.length === 0) return null;

  // The quote is suppressed when the reply targets the session's latest message
  // (already live in the agent's context); routing metadata is still recorded.
  const quote = options?.skipQuote !== true ? replyQuote(message) : null;

  return {
    text: quote != null ? `${quote}\n\n${text}` : text,
    channel: CHANNEL_NAME,
    receivedAt: new Date(),
    media: [],
    metadata: withReplyTarget({ messageId: message.message_id }, message),
  };
};

export const mapMediaMessage = (
  message: Pick<Message, "caption" | "message_id" | "reply_to_message">,
  attachment: MediaAttachment,
  options?: { skipQuote?: boolean },
): InboundMessage => {
  const quote = options?.skipQuote !== true ? replyQuote(message) : null;
  const caption = message.caption?.trim() ?? "";

  return {
    text: quote != null ? `${quote}\n\n${caption}`.trim() : caption,
    channel: CHANNEL_NAME,
    receivedAt: new Date(),
    media: [attachment],
    metadata: withReplyTarget({ messageId: message.message_id }, message),
  };
};

const withReplyTarget = (
  metadata: Record<string, unknown>,
  message: Pick<Message, "reply_to_message">,
): Record<string, unknown> => {
  const target = replyTargetId(message);

  return target != null ? { ...metadata, replyToMessageId: target } : metadata;
};

const emojiSet = (reactions: ReactionType[] | undefined): Set<string> =>
  new Set(
    (reactions ?? []).flatMap((reaction) => (reaction.type === "emoji" ? [reaction.emoji] : [])),
  );

/**
 * Frame a reaction update as an inbound message. Diffs old/new reactions so the
 * agent sees what the user added or removed; returns null when nothing changed.
 * When `context.lastExchange` is supplied (the reaction targets an older
 * message), it is prepended as `Reacted to:` context with an interpretation
 * hint; a reaction to the session's latest message is left bare (already live).
 */
export const mapReaction = (
  event: MessageReactionUpdated,
  context?: { lastExchange: string | null },
): InboundMessage | null => {
  const next = emojiSet(event.new_reaction);
  const previous = emojiSet(event.old_reaction);

  const added = [...next].filter((emoji) => !previous.has(emoji));
  const removed = [...previous].filter((emoji) => !next.has(emoji));

  if (added.length === 0 && removed.length === 0) return null;

  const parts = [
    added.length > 0 ? `reacted ${added.join(" ")}` : null,
    removed.length > 0 ? `removed reaction ${removed.join(" ")}` : null,
  ].filter((part) => part != null);

  const prose = `The user ${parts.join(" and ")} to a previous message.`;
  const exchange = context?.lastExchange?.trim();
  const text =
    exchange != null && exchange.length > 0
      ? `Reacted to:\n${exchange}\n\n${prose} Interpret it in the context of the last exchange and respond accordingly.`
      : prose;

  return {
    text,
    channel: CHANNEL_NAME,
    receivedAt: new Date(),
    media: [],
    metadata: { reaction: true, replyToMessageId: String(event.message_id) },
  };
};

/**
 * Frame a button tap so the agent can distinguish it from typed input. When
 * `context.prompt` is supplied (the tap targets an older button message), the
 * original prompt is prepended so the agent sees the question its tap answers;
 * a tap on the session's latest button message is left bare (already live).
 */
export const mapButtonTap = (
  value: string,
  messageId: number | null,
  context?: { prompt: string | null },
): InboundMessage => {
  const prose = `The user tapped the option \`${value}\` out of the options you displayed.`;
  const prompt = context?.prompt?.trim();
  const text = prompt != null && prompt.length > 0 ? `${prompt}\n\n${prose}` : prose;

  return {
    text,
    channel: CHANNEL_NAME,
    receivedAt: new Date(),
    media: [],
    metadata: { buttonValue: value, ...(messageId != null ? { messageId } : {}) },
  };
};

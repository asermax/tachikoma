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

/**
 * A `Label:\n> <quote>` block of `text`, or null when `text` is blank. Shared by reply and
 * reaction quotes so the blank-check and truncation live in one place.
 */
const quoteBlock = (label: string, text: string | null | undefined): string | null => {
  const trimmed = text?.trim();
  if (trimmed == null || trimmed.length === 0) return null;

  return `${label}:\n> ${truncateQuote(trimmed)}`;
};

/** Quote of a replied-to message, prepended to the agent prompt as context. */
export const replyQuote = (message: Pick<Message, "reply_to_message">): string | null => {
  const replied = message.reply_to_message;
  return replied == null ? null : quoteBlock("Replied to", replied.text ?? replied.caption);
};

/** Quote of a reacted-to message's recovered text, mirroring {@link replyQuote}. Null when blank. */
export const reactionQuote = (text: string | null | undefined): string | null =>
  quoteBlock("Reacted to", text);

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
 * When `context.reactedToText` is supplied (the channel recovered the targeted
 * message's text — the reaction is not aimed at the session's most recent
 * message), it is prepended as a `Reacted to:` quote so the agent knows which
 * message the emoji targets; otherwise the bare reaction prose is used.
 */
export const mapReaction = (
  event: MessageReactionUpdated,
  context?: { reactedToText?: string | null },
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
  const quote = reactionQuote(context?.reactedToText);
  const text =
    quote != null ? `${quote}\n\n${prose} Interpret it in context and respond accordingly.` : prose;

  return {
    text,
    channel: CHANNEL_NAME,
    receivedAt: new Date(),
    media: [],
    metadata: { reaction: true, replyToMessageId: String(event.message_id) },
  };
};

/**
 * Frame a button tap so the agent can distinguish it from typed input. A tap on an earlier branch's
 * button is routed to that branch by the channel (forced shift + context injection), so no inline
 * prompt recovery is needed here.
 */
export const mapButtonTap = (value: string, messageId: number | null): InboundMessage => ({
  text: `The user tapped the option \`${value}\` out of the options you displayed.`,
  channel: CHANNEL_NAME,
  receivedAt: new Date(),
  media: [],
  metadata: { buttonValue: value, ...(messageId != null ? { messageId } : {}) },
});

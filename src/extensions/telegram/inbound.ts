import type { Message } from "grammy/types";

import type { InboundMessage, MediaAttachment } from "../../domain/message.ts";

export const CHANNEL_NAME = "telegram";

export const mapTextMessage = (
  message: Pick<Message, "text" | "message_id">,
): InboundMessage | null => {
  const text = message.text?.trim() ?? "";

  if (text.length === 0) return null;

  return {
    text,
    channel: CHANNEL_NAME,
    receivedAt: new Date(),
    media: [],
    metadata: { messageId: message.message_id },
  };
};

export const mapMediaMessage = (
  message: Pick<Message, "caption" | "message_id">,
  attachment: MediaAttachment,
): InboundMessage => ({
  text: message.caption?.trim() ?? "",
  channel: CHANNEL_NAME,
  receivedAt: new Date(),
  media: [attachment],
  metadata: { messageId: message.message_id },
});

/** Frame a button tap so the agent can distinguish it from typed input. */
export const mapButtonTap = (value: string, messageId: number | null): InboundMessage => ({
  text: `The user tapped the option \`${value}\` out of the options you displayed.`,
  channel: CHANNEL_NAME,
  receivedAt: new Date(),
  media: [],
  metadata: { buttonValue: value, ...(messageId != null ? { messageId } : {}) },
});

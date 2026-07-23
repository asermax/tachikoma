export const MEDIA_KINDS = {
  photo: "photo",
  audio: "audio",
  voice: "voice",
  document: "document",
  video: "video",
  animation: "animation",
  sticker: "sticker",
} as const;

export type MediaKind = keyof typeof MEDIA_KINDS;

export interface MediaAttachment {
  kind: MediaKind;
  path: string;
  mimeType?: string;
  description?: string;
  metadata?: Record<string, unknown>;
}

/**
 * A branching decision surfaced to the user as a turn-scoped header on the response (R8). Set on
 * `InboundMessage.metadata.decisionHeader` by the boundary middleware; the coordinator forwards it to
 * the channel, which anchors it above the streamed text for that one response (the streaming renderer
 * recomposes it on every edit). `rollbackable` is true only for automatic decisions (a `/rollback`
 * target); manual decisions surface for awareness only.
 *
 * When `reaction` is set, channels that support emoji reactions (Telegram) place it on the outbound
 * message instead of rendering the italic label text — the label/note then serve only as a fallback
 * (e.g. an empty turn with no message to react to, or a text-only channel). Absent ⇒ render the text
 * header as today.
 */
export interface DecisionHeader {
  /** Short emoji-prefixed label, e.g. "📌 Checkpoint set". */
  label: string;
  /** One-line explanation of what happened. */
  note: string;
  /** True only for automatic decisions (a `/rollback` target); manual decisions are informational. */
  rollbackable: boolean;
  /**
   * When set, surface the decision as an emoji reaction on the outbound message (best-effort) rather
   * than as the italic header text. Must be an emoji valid as a Telegram reaction
   * (`setMessageReaction` rejects others with 400 MESSAGE_REACTION_INVALID).
   */
  reaction?: string;
}

export interface InboundMessage {
  text: string;
  channel: string;
  receivedAt: Date;
  media: MediaAttachment[];
  metadata: Record<string, unknown>;
}

/**
 * Read + validate a {@link DecisionHeader} from inbound metadata (set by the boundary middleware).
 * Returns null when absent or malformed so the coordinator forwards a header only when well-formed.
 */
export const decisionHeaderFrom = (metadata: Record<string, unknown>): DecisionHeader | null => {
  const value = metadata.decisionHeader;
  if (value == null || typeof value !== "object") return null;

  const header = value as Record<string, unknown>;
  if (typeof header.label !== "string" || typeof header.note !== "string") return null;

  return {
    label: header.label,
    note: header.note,
    rollbackable: header.rollbackable === true,
    ...(typeof header.reaction === "string" ? { reaction: header.reaction } : {}),
  };
};

export const textMessage = (channel: string, text: string): InboundMessage => ({
  text,
  channel,
  receivedAt: new Date(),
  media: [],
  metadata: {},
});

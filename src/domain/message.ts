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

export interface InboundMessage {
  text: string;
  channel: string;
  receivedAt: Date;
  media: MediaAttachment[];
  metadata: Record<string, unknown>;
}

export const textMessage = (channel: string, text: string): InboundMessage => ({
  text,
  channel,
  receivedAt: new Date(),
  media: [],
  metadata: {},
});

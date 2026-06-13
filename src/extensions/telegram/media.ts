import { randomBytes } from "node:crypto";
import { mkdir, readdir, stat, unlink, writeFile } from "node:fs/promises";
import { dirname, extname, join } from "node:path";
import type { Message } from "grammy/types";

import type { MediaAttachment, MediaKind } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";

// Telegram bots can only download files up to 20 MB.
export const TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024;

const MEDIA_RETENTION_DAYS = 30;

export type MediaMessage = Pick<
  Message,
  "animation" | "sticker" | "video_note" | "photo" | "voice" | "video" | "audio" | "document"
>;

export interface ResolvedMedia {
  kind: MediaKind;
  label: string;
  fileId: string;
  fileSize: number | null;
  fileName: string | null;
  extension: string;
  mimeType: string | null;
  metadata: Record<string, unknown>;
  /** Human-readable facts for the attachment description, e.g. "800 × 600". */
  summary: string[];
}

export class MediaTooLargeError extends Error {
  readonly fileSize: number;

  constructor(fileSize: number) {
    super(
      `File too large to download (${(fileSize / (1024 * 1024)).toFixed(1)} MB). ` +
        "Telegram bots can only download files up to 20 MB.",
    );
    this.fileSize = fileSize;
  }
}

/**
 * Resolve the first matching media payload on a message, in priority order:
 * animation before document (animations set both fields), video_note before
 * video (defensive), document last (most generic).
 */
export const resolveMedia = (message: MediaMessage): ResolvedMedia | null => {
  if (message.animation != null) {
    const media = message.animation;

    return {
      kind: "animation",
      label: "Animation",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: media.file_name ?? null,
      extension: ".mp4",
      mimeType: media.mime_type ?? "video/mp4",
      metadata: { width: media.width, height: media.height, duration: media.duration },
      summary: [`${media.duration} seconds`, `${media.width} × ${media.height}`],
    };
  }

  if (message.sticker != null) {
    const media = message.sticker;
    const format = media.is_animated
      ? { extension: ".tgs", mimeType: "application/x-tgsticker", description: "animated (.tgs)" }
      : media.is_video
        ? { extension: ".webm", mimeType: "video/webm", description: "video (.webm)" }
        : { extension: ".webp", mimeType: "image/webp", description: "regular (.webp)" };

    return {
      kind: "sticker",
      label: "Sticker",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: null,
      extension: format.extension,
      mimeType: format.mimeType,
      metadata: {
        ...(media.emoji != null ? { emoji: media.emoji } : {}),
        format: format.description,
      },
      summary: [
        ...(media.emoji != null ? [`Emoji: ${media.emoji}`] : []),
        `Format: ${format.description}`,
      ],
    };
  }

  if (message.video_note != null) {
    const media = message.video_note;

    // The domain has no dedicated video-note kind — closest is video.
    return {
      kind: "video",
      label: "Video note",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: null,
      extension: ".mp4",
      mimeType: "video/mp4",
      metadata: { duration: media.duration, diameter: media.length },
      summary: [`${media.duration} seconds`, `Diameter: ${media.length}px`],
    };
  }

  const photo = message.photo?.at(-1);
  if (photo != null) {
    return {
      kind: "photo",
      label: "Photo",
      fileId: photo.file_id,
      fileSize: photo.file_size ?? null,
      fileName: null,
      extension: ".jpg",
      mimeType: "image/jpeg",
      metadata: { width: photo.width, height: photo.height },
      summary: [`${photo.width} × ${photo.height}`, formatFileSize(photo.file_size ?? null)],
    };
  }

  if (message.voice != null) {
    const media = message.voice;

    return {
      kind: "voice",
      label: "Voice message",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: null,
      extension: ".ogg",
      mimeType: media.mime_type ?? "audio/ogg",
      metadata: { duration: media.duration },
      summary: [`${media.duration} seconds`, ...(media.mime_type != null ? [media.mime_type] : [])],
    };
  }

  if (message.video != null) {
    const media = message.video;

    return {
      kind: "video",
      label: "Video",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: media.file_name ?? null,
      extension: extensionFromName(media.file_name, ".mp4"),
      mimeType: media.mime_type ?? null,
      metadata: { width: media.width, height: media.height, duration: media.duration },
      summary: [`${media.duration} seconds`, `${media.width} × ${media.height}`],
    };
  }

  if (message.audio != null) {
    const media = message.audio;

    return {
      kind: "audio",
      label: "Audio file",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: media.file_name ?? null,
      extension: extensionFromName(media.file_name, ".mp3"),
      mimeType: media.mime_type ?? null,
      metadata: {
        duration: media.duration,
        ...(media.title != null ? { title: media.title } : {}),
        ...(media.performer != null ? { performer: media.performer } : {}),
      },
      summary: [
        ...(media.title != null ? [media.title] : []),
        ...(media.performer != null ? [media.performer] : []),
        `${media.duration} seconds`,
      ],
    };
  }

  if (message.document != null) {
    const media = message.document;

    return {
      kind: "document",
      label: "Document",
      fileId: media.file_id,
      fileSize: media.file_size ?? null,
      fileName: media.file_name ?? null,
      extension: extensionFromName(media.file_name, ""),
      mimeType: media.mime_type ?? null,
      metadata: { ...(media.file_name != null ? { fileName: media.file_name } : {}) },
      summary: [
        ...(media.file_name != null ? [media.file_name] : []),
        ...(media.mime_type != null ? [media.mime_type] : []),
        ...(media.file_size != null ? [formatFileSize(media.file_size)] : []),
      ],
    };
  }

  return null;
};

/** Unique filename: original names are preserved behind a random prefix. */
export const generateMediaFilename = (
  media: Pick<ResolvedMedia, "fileName" | "extension">,
): string => {
  const unique = randomBytes(6).toString("hex");

  return media.fileName != null ? `${unique}-${media.fileName}` : `${unique}${media.extension}`;
};

export const buildAttachment = (media: ResolvedMedia, path: string): MediaAttachment => ({
  kind: media.kind,
  path,
  ...(media.mimeType != null ? { mimeType: media.mimeType } : {}),
  description: `${media.label} (${media.summary.join(", ")})`,
  metadata: { ...media.metadata, ...(media.fileSize != null ? { fileSize: media.fileSize } : {}) },
});

export interface FileApi {
  getFile(fileId: string): Promise<{ file_path?: string }>;
}

/** Download a Telegram file to disk, pre-checking the bot download size limit. */
export const downloadMedia = async (
  api: FileApi,
  token: string,
  media: Pick<ResolvedMedia, "fileId" | "fileSize">,
  destPath: string,
): Promise<void> => {
  if (media.fileSize != null && media.fileSize > TELEGRAM_MAX_DOWNLOAD_BYTES) {
    throw new MediaTooLargeError(media.fileSize);
  }

  const file = await api.getFile(media.fileId);
  if (file.file_path == null) throw new Error("Telegram returned no file path for download");

  const response = await fetch(`https://api.telegram.org/file/bot${token}/${file.file_path}`);
  if (!response.ok) throw new Error(`File download failed with status ${response.status}`);

  await mkdir(dirname(destPath), { recursive: true });
  await writeFile(destPath, Buffer.from(await response.arrayBuffer()));
};

/** Bootstrap: ensure the media directory exists and prune stale downloads. */
export const ensureMediaDir = async (mediaDir: string, log: Logger): Promise<void> => {
  await mkdir(mediaDir, { recursive: true });

  const cutoff = Date.now() - MEDIA_RETENTION_DAYS * 24 * 60 * 60 * 1000;
  let cleaned = 0;

  for (const entry of await readdir(mediaDir, { withFileTypes: true })) {
    if (!entry.isFile()) continue;

    const path = join(mediaDir, entry.name);

    try {
      if ((await stat(path)).mtimeMs < cutoff) {
        await unlink(path);
        cleaned += 1;
      }
    } catch (error) {
      log.warn({ err: error, path }, "failed to clean old media file");
    }
  }

  log.debug({ mediaDir, cleaned }, "media directory ready");
};

const extensionFromName = (fileName: string | undefined, fallback: string): string => {
  if (fileName != null) {
    const extension = extname(fileName);
    if (extension !== "") return extension;
  }

  return fallback;
};

const formatFileSize = (size: number | null): string => {
  if (size == null) return "size unknown";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;

  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};

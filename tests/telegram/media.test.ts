import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mkdir = vi.fn();
const writeFile = vi.fn();
const readdir = vi.fn();
const stat = vi.fn();
const unlink = vi.fn();

vi.mock("node:fs/promises", () => ({
  mkdir: (...args: unknown[]) => mkdir(...args),
  writeFile: (...args: unknown[]) => writeFile(...args),
  readdir: (...args: unknown[]) => readdir(...args),
  stat: (...args: unknown[]) => stat(...args),
  unlink: (...args: unknown[]) => unlink(...args),
}));

import {
  buildAttachment,
  downloadMedia,
  ensureMediaDir,
  type FileApi,
  generateMediaFilename,
  type MediaMessage,
  MediaTooLargeError,
  resolveMedia,
  TELEGRAM_MAX_DOWNLOAD_BYTES,
} from "../../src/extensions/telegram/media.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

afterEach(() => {
  vi.clearAllMocks();
});

describe("MediaTooLargeError", () => {
  it("reports the size in megabytes and the bot download limit", () => {
    const error = new MediaTooLargeError(25 * 1024 * 1024);

    expect(error.fileSize).toBe(25 * 1024 * 1024);
    expect(error.message).toContain("25.0 MB");
    expect(error.message).toContain("20 MB");
  });
});

describe("resolveMedia branch coverage", () => {
  it("falls back to defaults when an animation omits optional fields", () => {
    const message = {
      animation: { file_id: "a1", file_unique_id: "a", width: 480, height: 270, duration: 3 },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "animation",
      fileSize: null,
      fileName: null,
      extension: ".mp4",
      mimeType: "video/mp4",
    });
  });

  it("keeps explicit animation file size, name, and mime type", () => {
    const message = {
      animation: {
        file_id: "a1",
        file_unique_id: "a",
        width: 480,
        height: 270,
        duration: 3,
        file_size: 9000,
        file_name: "loop.gif",
        mime_type: "image/gif",
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      fileSize: 9000,
      fileName: "loop.gif",
      mimeType: "image/gif",
    });
  });

  it("resolves an animated (.tgs) sticker without an emoji", () => {
    const message = {
      sticker: {
        file_id: "s1",
        file_unique_id: "s",
        type: "regular" as const,
        width: 512,
        height: 512,
        is_animated: true,
        is_video: false,
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "sticker",
      extension: ".tgs",
      mimeType: "application/x-tgsticker",
      metadata: { format: "animated (.tgs)" },
      summary: ["Format: animated (.tgs)"],
    });
  });

  it("resolves a video (.webm) sticker", () => {
    const message = {
      sticker: {
        file_id: "s1",
        file_unique_id: "s",
        type: "regular" as const,
        width: 512,
        height: 512,
        is_animated: false,
        is_video: true,
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      extension: ".webm",
      mimeType: "video/webm",
      summary: ["Format: video (.webm)"],
    });
  });

  it("resolves a regular (.webp) sticker", () => {
    const message = {
      sticker: {
        file_id: "s1",
        file_unique_id: "s",
        type: "regular" as const,
        width: 512,
        height: 512,
        is_animated: false,
        is_video: false,
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      extension: ".webp",
      mimeType: "image/webp",
      summary: ["Format: regular (.webp)"],
    });
  });

  it("uses size unknown in a photo summary when the file size is absent", () => {
    const message = {
      photo: [{ file_id: "p", file_unique_id: "p", width: 100, height: 100 }],
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "photo",
      fileSize: null,
      summary: ["100 × 100", "size unknown"],
    });
  });

  it("includes the voice mime type in the summary when present", () => {
    const message = {
      voice: { file_id: "v1", file_unique_id: "v", duration: 5, mime_type: "audio/ogg" },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      summary: ["5 seconds", "audio/ogg"],
    });
  });

  it("includes the document mime type and a KB-range size in the summary", () => {
    const message = {
      document: {
        file_id: "d1",
        file_unique_id: "d",
        file_name: "report.pdf",
        mime_type: "application/pdf",
        file_size: 2048,
      },
    } as MediaMessage;

    expect(resolveMedia(message)?.summary).toEqual(["report.pdf", "application/pdf", "2 KB"]);
  });

  it("includes the sticker emoji in metadata and summary when present", () => {
    const message = {
      sticker: {
        file_id: "s1",
        file_unique_id: "s",
        type: "regular" as const,
        width: 512,
        height: 512,
        is_animated: false,
        is_video: false,
        emoji: "😀",
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      metadata: { emoji: "😀", format: "regular (.webp)" },
      summary: ["Emoji: 😀", "Format: regular (.webp)"],
    });
  });

  it("omits the voice mime type from the summary when absent", () => {
    const message = {
      voice: { file_id: "v1", file_unique_id: "v", duration: 4 },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "voice",
      mimeType: "audio/ogg",
      summary: ["4 seconds"],
    });
  });

  it("maps a video, deriving the extension from its file name", () => {
    const message = {
      video: {
        file_id: "vid1",
        file_unique_id: "vid",
        width: 1920,
        height: 1080,
        duration: 12,
        file_name: "clip.mov",
        mime_type: "video/quicktime",
        file_size: 500_000,
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "video",
      label: "Video",
      fileName: "clip.mov",
      extension: ".mov",
      mimeType: "video/quicktime",
      summary: ["12 seconds", "1920 × 1080"],
    });
  });

  it("falls back to .mp4 and null mime type for a bare video", () => {
    const message = {
      video: { file_id: "vid1", file_unique_id: "vid", width: 640, height: 480, duration: 2 },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      extension: ".mp4",
      mimeType: null,
      fileName: null,
    });
  });

  it("maps an audio file with title and performer in the metadata and summary", () => {
    const message = {
      audio: {
        file_id: "au1",
        file_unique_id: "au",
        duration: 200,
        file_name: "song.flac",
        mime_type: "audio/flac",
        title: "Track",
        performer: "Artist",
        file_size: 3000,
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "audio",
      extension: ".flac",
      mimeType: "audio/flac",
      metadata: { duration: 200, title: "Track", performer: "Artist" },
      summary: ["Track", "Artist", "200 seconds"],
    });
  });

  it("falls back to .mp3 and omits optional audio fields when absent", () => {
    const message = {
      audio: { file_id: "au1", file_unique_id: "au", duration: 30 },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      extension: ".mp3",
      mimeType: null,
      metadata: { duration: 30 },
      summary: ["30 seconds"],
    });
  });

  it("maps video notes onto the video kind", () => {
    const message = {
      video_note: { file_id: "n1", file_unique_id: "n", length: 240, duration: 8 },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "video",
      label: "Video note",
      fileSize: null,
      metadata: { duration: 8, diameter: 240 },
      summary: ["8 seconds", "Diameter: 240px"],
    });
  });

  it("returns null when no supported media is present", () => {
    expect(resolveMedia({} as MediaMessage)).toBeNull();
  });

  it("formats byte-range and megabyte-range document sizes", () => {
    const small = resolveMedia({
      document: { file_id: "d1", file_unique_id: "d", file_name: "tiny.txt", file_size: 512 },
    } as MediaMessage);
    expect(small?.summary).toContain("512 B");

    const large = resolveMedia({
      document: {
        file_id: "d2",
        file_unique_id: "d",
        file_name: "big.zip",
        file_size: 5 * 1024 * 1024,
      },
    } as MediaMessage);
    expect(large?.summary).toContain("5.0 MB");
  });

  it("maps a document without a file name, leaving the extension empty", () => {
    const message = {
      document: { file_id: "d1", file_unique_id: "d" },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "document",
      fileName: null,
      extension: "",
      mimeType: null,
      metadata: {},
      summary: [],
    });
  });

  it("falls back to the empty extension when a document name has no suffix", () => {
    const message = {
      document: { file_id: "d1", file_unique_id: "d", file_name: "README" },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      extension: "",
      metadata: { fileName: "README" },
      summary: ["README"],
    });
  });
});

describe("buildAttachment branch coverage", () => {
  it("includes the mime type and file size when the resolved media carries them", () => {
    const resolved = resolveMedia({
      photo: [{ file_id: "p", file_unique_id: "p", width: 800, height: 600, file_size: 120_000 }],
    } as MediaMessage);

    if (resolved == null) throw new Error("expected media to resolve");

    expect(buildAttachment(resolved, "/data/media/abc.jpg")).toEqual({
      kind: "photo",
      path: "/data/media/abc.jpg",
      mimeType: "image/jpeg",
      description: "Photo (800 × 600, 117 KB)",
      metadata: { width: 800, height: 600, fileSize: 120_000 },
    });
  });

  it("omits the mime type and file size when the resolved media lacks them", () => {
    const resolved = resolveMedia({
      document: { file_id: "d1", file_unique_id: "d", file_name: "notes" },
    } as MediaMessage);

    if (resolved == null) throw new Error("expected media to resolve");

    expect(buildAttachment(resolved, "/data/media/notes")).toEqual({
      kind: "document",
      path: "/data/media/notes",
      description: "Document (notes)",
      metadata: { fileName: "notes" },
    });
  });
});

describe("generateMediaFilename", () => {
  it("preserves the original file name behind a unique prefix", () => {
    expect(generateMediaFilename({ fileName: "report.pdf", extension: ".pdf" })).toMatch(
      /^[0-9a-f]{12}-report\.pdf$/,
    );
  });

  it("falls back to the resolved extension when there is no file name", () => {
    expect(generateMediaFilename({ fileName: null, extension: ".jpg" })).toMatch(
      /^[0-9a-f]{12}\.jpg$/,
    );
  });
});

describe("downloadMedia", () => {
  const api: FileApi = { getFile: vi.fn() };

  beforeEach(() => {
    vi.mocked(api.getFile).mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws MediaTooLargeError before any network call when over the limit", async () => {
    await expect(
      downloadMedia(
        api,
        "token",
        { fileId: "f", fileSize: TELEGRAM_MAX_DOWNLOAD_BYTES + 1 },
        "/tmp/x",
      ),
    ).rejects.toBeInstanceOf(MediaTooLargeError);

    expect(api.getFile).not.toHaveBeenCalled();
  });

  it("throws when Telegram returns no file path", async () => {
    vi.mocked(api.getFile).mockResolvedValue({});

    await expect(
      downloadMedia(api, "token", { fileId: "f", fileSize: null }, "/tmp/x"),
    ).rejects.toThrow("Telegram returned no file path for download");
  });

  it("throws when the download responds with a non-ok status", async () => {
    vi.mocked(api.getFile).mockResolvedValue({ file_path: "photos/p.jpg" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));

    await expect(
      downloadMedia(api, "token", { fileId: "f", fileSize: 10 }, "/tmp/x"),
    ).rejects.toThrow("File download failed with status 404");
  });

  it("writes the downloaded bytes to disk on success", async () => {
    vi.mocked(api.getFile).mockResolvedValue({ file_path: "photos/p.jpg" });

    const bytes = new Uint8Array([1, 2, 3]).buffer;
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, arrayBuffer: vi.fn().mockResolvedValue(bytes) });
    vi.stubGlobal("fetch", fetchMock);
    mkdir.mockResolvedValue(undefined);
    writeFile.mockResolvedValue(undefined);

    await downloadMedia(api, "secret", { fileId: "f", fileSize: 10 }, "/data/media/out.jpg");

    expect(fetchMock).toHaveBeenCalledWith("https://api.telegram.org/file/botsecret/photos/p.jpg");
    expect(mkdir).toHaveBeenCalledWith("/data/media", { recursive: true });
    expect(writeFile).toHaveBeenCalledWith("/data/media/out.jpg", Buffer.from(bytes));
  });
});

describe("ensureMediaDir", () => {
  it("creates the directory and prunes only stale files", async () => {
    const now = 1_000_000_000_000;
    vi.spyOn(Date, "now").mockReturnValue(now);
    const cutoff = now - 30 * 24 * 60 * 60 * 1000;

    mkdir.mockResolvedValue(undefined);
    readdir.mockResolvedValue([
      { name: "old.jpg", isFile: () => true },
      { name: "fresh.jpg", isFile: () => true },
      { name: "subdir", isFile: () => false },
    ]);
    stat.mockImplementation((path: string) =>
      Promise.resolve({ mtimeMs: path.endsWith("old.jpg") ? cutoff - 1 : cutoff + 1 }),
    );
    unlink.mockResolvedValue(undefined);

    await ensureMediaDir("/data/media", fakeLog);

    expect(mkdir).toHaveBeenCalledWith("/data/media", { recursive: true });
    expect(unlink).toHaveBeenCalledTimes(1);
    expect(unlink).toHaveBeenCalledWith("/data/media/old.jpg");
    expect(fakeLog.debug).toHaveBeenCalledWith(
      { mediaDir: "/data/media", cleaned: 1 },
      "media directory ready",
    );

    vi.mocked(Date.now).mockRestore();
  });

  it("logs a warning and continues when cleaning a file fails", async () => {
    mkdir.mockResolvedValue(undefined);
    readdir.mockResolvedValue([{ name: "broken.jpg", isFile: () => true }]);
    const failure = new Error("stat failed");
    stat.mockRejectedValue(failure);

    await ensureMediaDir("/data/media", fakeLog);

    expect(fakeLog.warn).toHaveBeenCalledWith(
      { err: failure, path: "/data/media/broken.jpg" },
      "failed to clean old media file",
    );
    expect(unlink).not.toHaveBeenCalled();
  });
});

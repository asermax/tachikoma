import type { MessageReactionUpdated } from "grammy/types";
import { describe, expect, it } from "vitest";

import {
  mapButtonTap,
  mapMediaMessage,
  mapReaction,
  mapTextMessage,
} from "../../src/extensions/telegram/inbound.ts";
import {
  buildAttachment,
  generateMediaFilename,
  type MediaMessage,
  resolveMedia,
} from "../../src/extensions/telegram/media.ts";

describe("mapTextMessage", () => {
  it("maps text and records the Telegram message id", () => {
    const inbound = mapTextMessage({ message_id: 5, text: "  hello there  " });

    expect(inbound).toMatchObject({
      text: "hello there",
      channel: "telegram",
      media: [],
      metadata: { messageId: 5 },
    });
  });

  it("returns null for empty or whitespace-only text", () => {
    expect(mapTextMessage({ message_id: 5, text: "   " })).toBeNull();
    expect(mapTextMessage({ message_id: 5 })).toBeNull();
  });

  it("prepends a quote and records the reply target when replying", () => {
    const inbound = mapTextMessage({
      message_id: 7,
      text: "what about this?",
      reply_to_message: { message_id: 3, text: "the plan is ready" } as never,
    });

    expect(inbound?.text).toBe("Replied to:\n> the plan is ready\n\nwhat about this?");
    expect(inbound?.metadata).toEqual({ messageId: 7, replyToMessageId: "3" });
  });

  it("omits the reply target when the message is not a reply", () => {
    const inbound = mapTextMessage({ message_id: 7, text: "hi" });

    expect(inbound?.metadata).toEqual({ messageId: 7 });
  });

  it("truncates a long replied-to quote with a head…tail ellipsis", () => {
    const longText = `${"H".repeat(400)}${"T".repeat(400)}`;
    const inbound = mapTextMessage({
      message_id: 8,
      text: "follow up",
      reply_to_message: { message_id: 4, text: longText } as never,
    });

    const quoteLine = inbound?.text.split("\n")[1] ?? "";
    expect(quoteLine).toContain("…");
    expect(quoteLine.startsWith("> H")).toBe(true);
    expect(quoteLine.endsWith("T")).toBe(true);
    expect(inbound?.text.length).toBeLessThan(longText.length);
  });

  it("ignores a replied-to message whose text is blank", () => {
    const inbound = mapTextMessage({
      message_id: 9,
      text: "hi",
      reply_to_message: { message_id: 4, text: "   " } as never,
    });

    expect(inbound?.text).toBe("hi");
    expect(inbound?.metadata).toEqual({ messageId: 9, replyToMessageId: "4" });
  });

  it("skips the reply quote but keeps routing metadata when skipQuote is set", () => {
    const inbound = mapTextMessage(
      {
        message_id: 7,
        text: "what about this?",
        reply_to_message: { message_id: 3, text: "the plan is ready" } as never,
      },
      { skipQuote: true },
    );

    expect(inbound?.text).toBe("what about this?");
    expect(inbound?.metadata).toEqual({ messageId: 7, replyToMessageId: "3" });
  });
});

describe("mapReaction", () => {
  const reaction = (
    messageId: number,
    userId: number | undefined,
    next: string[],
    old: string[] = [],
  ): MessageReactionUpdated =>
    ({
      message_id: messageId,
      user: userId != null ? { id: userId } : undefined,
      new_reaction: next.map((emoji) => ({ type: "emoji", emoji })),
      old_reaction: old.map((emoji) => ({ type: "emoji", emoji })),
    }) as MessageReactionUpdated;

  it("surfaces an added reaction with the reacted-to message as the reply target", () => {
    const inbound = mapReaction(reaction(12, 42, ["👍"]));

    expect(inbound?.text).toBe("The user reacted 👍 to a previous message.");
    expect(inbound?.metadata).toEqual({ reaction: true, replyToMessageId: "12" });
  });

  it("reports both additions and removals", () => {
    const inbound = mapReaction(reaction(12, 42, ["🎉"], ["👍"]));

    expect(inbound?.text).toBe(
      "The user reacted 🎉 and removed reaction 👍 to a previous message.",
    );
  });

  it("returns null when nothing changed", () => {
    expect(mapReaction(reaction(12, 42, ["👍"], ["👍"]))).toBeNull();
  });

  it("reports a removal-only change without an addition clause", () => {
    const inbound = mapReaction(reaction(12, 42, [], ["👍"]));

    expect(inbound?.text).toBe("The user removed reaction 👍 to a previous message.");
  });

  it("ignores non-emoji reaction types and treats absent reaction lists as empty", () => {
    const event = {
      message_id: 15,
      new_reaction: [
        { type: "custom_emoji", custom_emoji_id: "abc" },
        { type: "emoji", emoji: "🔥" },
      ],
      old_reaction: undefined,
    } as unknown as MessageReactionUpdated;

    const inbound = mapReaction(event);

    expect(inbound?.text).toBe("The user reacted 🔥 to a previous message.");
  });

  it("prepends the reacted-to message quote and guidance when context is supplied", () => {
    const inbound = mapReaction(reaction(12, 42, ["👍"]), {
      reactedToText: "the plan is ready",
    });

    expect(inbound?.text).toBe(
      "Reacted to:\n> the plan is ready\n\n" +
        "The user reacted 👍 to a previous message. Interpret it in context and respond accordingly.",
    );
    expect(inbound?.metadata).toEqual({ reaction: true, replyToMessageId: "12" });
  });

  it("truncates a long reacted-to quote with a head…tail ellipsis", () => {
    const longText = `${"H".repeat(400)}${"T".repeat(400)}`;
    const inbound = mapReaction(reaction(12, 42, ["👍"]), { reactedToText: longText });

    const quoteLine = inbound?.text.split("\n")[1] ?? "";
    expect(quoteLine).toContain("…");
    expect(quoteLine.startsWith("> H")).toBe(true);
    expect(quoteLine.endsWith("T")).toBe(true);
    expect(inbound?.text.length).toBeLessThan(longText.length);
  });

  it("omits the quote when the reacted-to text is blank", () => {
    const inbound = mapReaction(reaction(12, 42, ["👍"]), { reactedToText: "   " });

    expect(inbound?.text).toBe("The user reacted 👍 to a previous message.");
  });
});

describe("mapMediaMessage", () => {
  const attachment = { kind: "photo" as const, path: "/tmp/x.jpg" };

  it("uses the caption as the message text", () => {
    const inbound = mapMediaMessage({ message_id: 6, caption: " look at this " }, attachment);

    expect(inbound.text).toBe("look at this");
    expect(inbound.media).toEqual([attachment]);
    expect(inbound.metadata).toEqual({ messageId: 6 });
  });

  it("defaults to empty text without a caption", () => {
    expect(mapMediaMessage({ message_id: 6 }, attachment).text).toBe("");
  });

  it("prepends a reply quote and records the reply target", () => {
    const inbound = mapMediaMessage(
      {
        message_id: 6,
        caption: "see this",
        reply_to_message: { message_id: 2, text: "the prior note" } as never,
      },
      attachment,
    );

    expect(inbound.text).toBe("Replied to:\n> the prior note\n\nsee this");
    expect(inbound.metadata).toEqual({ messageId: 6, replyToMessageId: "2" });
  });

  it("skips the reply quote when skipQuote is set", () => {
    const inbound = mapMediaMessage(
      {
        message_id: 6,
        caption: "see this",
        reply_to_message: { message_id: 2, text: "the prior note" } as never,
      },
      attachment,
      { skipQuote: true },
    );

    expect(inbound.text).toBe("see this");
    expect(inbound.metadata).toEqual({ messageId: 6, replyToMessageId: "2" });
  });
});

describe("mapButtonTap", () => {
  it("frames the tap so the agent can distinguish it from typed input", () => {
    const inbound = mapButtonTap("yes", 9);

    expect(inbound.text).toBe("The user tapped the option `yes` out of the options you displayed.");
    expect(inbound.metadata).toEqual({ buttonValue: "yes", messageId: 9 });
  });

  it("omits the message id from metadata when none is given", () => {
    expect(mapButtonTap("no", null).metadata).toEqual({ buttonValue: "no" });
  });
});

describe("resolveMedia", () => {
  it("picks the largest photo size and builds dimension metadata", () => {
    const message = {
      photo: [
        { file_id: "small", file_unique_id: "s", width: 90, height: 60, file_size: 1200 },
        { file_id: "large", file_unique_id: "l", width: 800, height: 600, file_size: 120_000 },
      ],
    } as MediaMessage;

    const resolved = resolveMedia(message);

    expect(resolved).toMatchObject({
      kind: "photo",
      label: "Photo",
      fileId: "large",
      fileSize: 120_000,
      extension: ".jpg",
      mimeType: "image/jpeg",
      metadata: { width: 800, height: 600 },
      summary: ["800 × 600", "117 KB"],
    });
  });

  it("maps voice notes with duration and mime type", () => {
    const message = {
      voice: { file_id: "v1", file_unique_id: "v", duration: 5, mime_type: "audio/ogg" },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "voice",
      label: "Voice message",
      extension: ".ogg",
      mimeType: "audio/ogg",
      summary: ["5 seconds", "audio/ogg"],
    });
  });

  it("derives document extension from the original file name", () => {
    const message = {
      document: {
        file_id: "d1",
        file_unique_id: "d",
        file_name: "report.pdf",
        mime_type: "application/pdf",
        file_size: 2048,
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "document",
      fileName: "report.pdf",
      extension: ".pdf",
      summary: ["report.pdf", "application/pdf", "2 KB"],
    });
  });

  it("prioritizes animation over document when both fields are set", () => {
    const message = {
      animation: {
        file_id: "a1",
        file_unique_id: "a",
        width: 480,
        height: 270,
        duration: 3,
      },
      document: { file_id: "d1", file_unique_id: "d" },
    } as MediaMessage;

    expect(resolveMedia(message)?.kind).toBe("animation");
  });

  it("maps video notes onto the video kind", () => {
    const message = {
      video_note: { file_id: "n1", file_unique_id: "n", length: 240, duration: 8 },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "video",
      label: "Video note",
      metadata: { duration: 8, diameter: 240 },
      summary: ["8 seconds", "Diameter: 240px"],
    });
  });

  it("resolves sticker format from its flags", () => {
    const message = {
      sticker: {
        file_id: "s1",
        file_unique_id: "s",
        type: "regular" as const,
        width: 512,
        height: 512,
        is_animated: false,
        is_video: true,
        emoji: "🎉",
      },
    } as MediaMessage;

    expect(resolveMedia(message)).toMatchObject({
      kind: "sticker",
      extension: ".webm",
      mimeType: "video/webm",
      summary: ["Emoji: 🎉", "Format: video (.webm)"],
    });
  });

  it("returns null when the message carries no supported media", () => {
    expect(resolveMedia({} as MediaMessage)).toBeNull();
  });
});

describe("generateMediaFilename", () => {
  it("preserves the original file name behind a unique prefix", () => {
    expect(generateMediaFilename({ fileName: "report.pdf", extension: ".pdf" })).toMatch(
      /^[0-9a-f]{12}-report\.pdf$/,
    );
  });

  it("falls back to the resolved extension", () => {
    expect(generateMediaFilename({ fileName: null, extension: ".jpg" })).toMatch(
      /^[0-9a-f]{12}\.jpg$/,
    );
  });
});

describe("buildAttachment", () => {
  it("builds the domain attachment with a human-readable description", () => {
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
});

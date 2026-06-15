import type { MessageEntity } from "grammy/types";
import { describe, expect, it } from "vitest";
import { TELEGRAM_MAX_MESSAGE_LENGTH } from "../../src/extensions/telegram/chunking.ts";
import {
  splitMessageWithEntities,
  type TelegramPayload,
  toTelegramEntities,
} from "../../src/extensions/telegram/entities.ts";

const find = (payload: TelegramPayload, type: string) =>
  payload.entities.find((e) => e.type === type);

/** Like `find`, but asserts the entity is present and returns it non-optionally. */
const must = (payload: TelegramPayload, type: string): MessageEntity => {
  const entity = find(payload, type);
  if (!entity) throw new Error(`expected a ${type} entity`);
  return entity;
};

describe("toTelegramEntities", () => {
  it("keeps plain text literal with no entities and no escaping", () => {
    const payload = toTelegramEntities("Total: 7-8 units! (see below).");

    expect(payload.text).toBe("Total: 7-8 units! (see below).");
    expect(payload.entities).toEqual([]);
  });

  it("emits a bold entity over **strong** text", () => {
    const payload = toTelegramEntities("a **bold** b");

    expect(payload.text).toBe("a bold b");
    const bold = must(payload, "bold");
    expect(bold).toMatchObject({ offset: 2, length: 4 });
    expect(payload.text.slice(bold.offset, bold.offset + bold.length)).toBe("bold");
  });

  it("emits an italic entity over _em_ text", () => {
    const payload = toTelegramEntities("an _italic_ word");

    expect(payload.text).toBe("an italic word");
    expect(find(payload, "italic")).toMatchObject({ offset: 3, length: 6 });
  });

  it("emits a strikethrough entity over ~~strike~~ text (GFM default preset)", () => {
    const payload = toTelegramEntities("~~deleted~~");

    expect(payload.text).toBe("deleted");
    expect(find(payload, "strikethrough")).toMatchObject({ offset: 0, length: 7 });
  });

  it("emits a code entity over inline code", () => {
    const payload = toTelegramEntities("run `npm test` now");

    expect(payload.text).toBe("run npm test now");
    expect(find(payload, "code")).toMatchObject({ offset: 4, length: 8 });
  });

  it("emits a pre entity with the fence language", () => {
    const payload = toTelegramEntities("```ts\nconst x = 1\n```");

    expect(payload.text).toBe("const x = 1");
    expect(find(payload, "pre")).toMatchObject({ offset: 0, length: 11, language: "ts" });
  });

  it("emits a pre entity without language for a bare fence", () => {
    const payload = toTelegramEntities("```\nplain\n```");

    expect(payload.text).toBe("plain");
    const pre = find(payload, "pre");
    expect(pre).toMatchObject({ offset: 0, length: 5 });
    expect(pre).not.toHaveProperty("language");
  });

  it("emits a text_link entity carrying the url", () => {
    const payload = toTelegramEntities("see [docs](https://example.com/x)");

    expect(payload.text).toBe("see docs");
    expect(find(payload, "text_link")).toMatchObject({
      offset: 4,
      length: 4,
      url: "https://example.com/x",
    });
  });

  it("renders a heading as bold (Telegram has no heading entity)", () => {
    const payload = toTelegramEntities("# Title");

    expect(payload.text).toBe("Title");
    expect(find(payload, "bold")).toMatchObject({ offset: 0, length: 5 });
  });

  it("emits a blockquote entity over quoted content", () => {
    const payload = toTelegramEntities("> a quoted line");

    expect(payload.text).toBe("a quoted line");
    expect(find(payload, "blockquote")).toMatchObject({ offset: 0, length: 13 });
  });

  it("supports nested formatting (bold wrapping italic)", () => {
    const payload = toTelegramEntities("**bold _and italic_**");

    expect(payload.text).toBe("bold and italic");
    expect(find(payload, "bold")).toMatchObject({ offset: 0, length: 15 });
    expect(find(payload, "italic")).toMatchObject({ offset: 5, length: 10 });
  });

  it("prefixes bullet list items with a bullet", () => {
    const payload = toTelegramEntities("- one\n- two\n- three");

    expect(payload.text).toBe("• one\n• two\n• three");
    // List items are plain literal text (no inline formatting here).
    expect(payload.entities).toEqual([]);
  });

  it("numbers ordered list items", () => {
    const payload = toTelegramEntities("1. first\n2. second");

    expect(payload.text).toBe("1. first\n2. second");
  });

  it("measures offsets in UTF-16 code units (emoji is two units)", () => {
    // a (1) + 😀 (2 UTF-16 units) + b (1) = length 4, all bold.
    const payload = toTelegramEntities("**a😀b**");

    expect(payload.text).toBe("a😀b");
    expect(find(payload, "bold")).toMatchObject({ offset: 0, length: 4 });
  });

  it("flattens a GFM table to bullets before conversion (via flattenTables)", () => {
    const input = ["| Day | Plan |", "|---|---|", "| Mon | Piano 7-8 PM |"].join("\n");
    const payload = toTelegramEntities(input);

    expect(payload.text).toContain("• ");
    expect(payload.text).toContain("Mon");
    expect(payload.text).toContain("Piano 7-8 PM");
    // The first cell became a bold label.
    expect(find(payload, "bold")).toBeDefined();
  });
});

describe("splitMessageWithEntities", () => {
  it("returns a single chunk when the text fits the limit", () => {
    const payload = toTelegramEntities("short");
    const chunks = splitMessageWithEntities(payload.text, payload.entities, 4096);

    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual(payload);
  });

  it("does not split a bold entity across two messages", () => {
    // A bold span straddling the 4096 boundary: the split snaps to just before it
    // so the whole bold span lands in the second chunk intact.
    const prefix = "x".repeat(4090);
    const markdown = `${prefix}**${"y".repeat(20)}**`;
    const { text, entities } = toTelegramEntities(markdown);
    const bold = must({ text, entities }, "bold");

    // Sanity: the bold span does cross 4096 in the converted text.
    expect(bold.offset).toBeLessThan(4096);
    expect(bold.offset + bold.length).toBeGreaterThan(4096);

    const chunks = splitMessageWithEntities(text, entities, 4096);

    // No chunk carries an entity whose range crosses its own bounds.
    for (const chunk of chunks) {
      for (const e of chunk.entities) {
        expect(e.offset).toBeGreaterThanOrEqual(0);
        expect(e.offset + e.length).toBeLessThanOrEqual(chunk.text.length);
      }
    }
    // The bold survives intact in exactly one chunk, rebased to that chunk.
    const boldChunk = chunks.find((c) => c.entities.some((e) => e.type === "bold"));
    expect(boldChunk).toBeDefined();
    const rebased = boldChunk?.entities.find((e) => e.type === "bold");
    expect(rebased).toBeDefined();
    if (boldChunk && rebased) {
      expect(boldChunk.text.slice(rebased.offset, rebased.offset + rebased.length)).toBe(
        "y".repeat(20),
      );
    }
  });

  it("keeps a fenced code block whole rather than splitting it", () => {
    // The code block straddles the 4096 boundary but is itself small enough to fit
    // one message — so it moves wholesale into the second chunk instead of being cut.
    const intro = "x".repeat(4090);
    const code = "z".repeat(100);
    const markdown = `${intro}\n\n\`\`\`\n${code}\n\`\`\``;
    const { text, entities } = toTelegramEntities(markdown);
    const pre = must({ text, entities }, "pre");

    expect(pre.offset).toBeLessThan(4096);
    expect(pre.offset + pre.length).toBeGreaterThan(4096);
    expect(pre.length).toBeLessThanOrEqual(4096);

    const chunks = splitMessageWithEntities(text, entities, 4096);

    // The pre entity is never split: it appears whole in one chunk.
    const preChunks = chunks.filter((c) => c.entities.some((e) => e.type === "pre"));
    expect(preChunks).toHaveLength(1);
  });

  it("drops an entity larger than the limit but keeps its text", () => {
    // A single bold span bigger than 4096 can't fit one message: it is dropped,
    // and its text content still renders across the chunks as plain.
    const inner = "q".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 50);
    const markdown = `**${inner}**`;
    const { text, entities } = toTelegramEntities(markdown);

    expect(entities.some((e) => e.type === "bold" && e.length > TELEGRAM_MAX_MESSAGE_LENGTH)).toBe(
      true,
    );

    const chunks = splitMessageWithEntities(text, entities, 4096);

    // No entities survive (the only one was oversize), but all text is delivered.
    for (const chunk of chunks) expect(chunk.entities).toEqual([]);
    expect(chunks.map((c) => c.text).join("")).toBe(text);
  });

  it("rebases entity offsets to each chunk's own text", () => {
    // Two short paragraphs with bold in the second; force a small limit so they split.
    const markdown = "**first**\n\n**second**";
    const { text, entities } = toTelegramEntities(markdown);
    const chunks = splitMessageWithEntities(text, entities, 8);

    expect(chunks.length).toBeGreaterThanOrEqual(2);
    for (const chunk of chunks) {
      for (const e of chunk.entities) {
        expect(e.offset).toBeGreaterThanOrEqual(0);
        expect(e.offset + e.length).toBeLessThanOrEqual(chunk.text.length);
      }
    }
  });

  it("prefers paragraph boundaries when entity-safe", () => {
    const a = "a".repeat(2000);
    const b = "b".repeat(2000);
    const { text, entities } = toTelegramEntities(`${a}\n\n${b}`);
    const chunks = splitMessageWithEntities(text, entities, 3000);

    expect(chunks).toHaveLength(2);
    expect(chunks[0].text.replace(/\n+$/, "")).toBe(a);
    expect(chunks[1].text).toBe(b);
  });
});

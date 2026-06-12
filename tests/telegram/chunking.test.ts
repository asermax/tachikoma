import { describe, expect, it } from "vitest";

import {
  splitMessage,
  TELEGRAM_MAX_MESSAGE_LENGTH,
} from "../../src/extensions/telegram/chunking.ts";

describe("splitMessage", () => {
  it("returns a single chunk when the text fits", () => {
    expect(splitMessage("hello world")).toEqual(["hello world"]);
  });

  it("splits at a paragraph boundary when the text exceeds the limit", () => {
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    const chunks = splitMessage(`${first}\n\n${second}`);

    expect(chunks).toEqual([first, second]);
  });

  it("packs as many paragraphs as fit into each chunk", () => {
    const paragraphs = ["1", "2", "3", "4", "5"].map((n) => n.repeat(1000));

    const chunks = splitMessage(paragraphs.join("\n\n"));

    expect(chunks).toEqual([paragraphs.slice(0, 4).join("\n\n"), paragraphs[4]]);
  });

  it("reconstructs the original text when splitting on paragraphs", () => {
    const text = ["a".repeat(2000), "b".repeat(2000), "c".repeat(2000)].join("\n\n");

    expect(splitMessage(text).join("\n\n")).toBe(text);
  });

  it("falls back to line boundaries inside an oversized paragraph", () => {
    const lines = ["x".repeat(1500), "y".repeat(1500), "z".repeat(1500)];

    const chunks = splitMessage(lines.join("\n"));

    expect(chunks).toEqual([`${lines[0]}\n${lines[1]}`, lines[2]]);
  });

  it("hard splits text without any boundaries", () => {
    const chunks = splitMessage("x".repeat(9000));

    expect(chunks.map((chunk) => chunk.length)).toEqual([4096, 4096, 808]);
  });

  it("never exceeds the limit and never cuts surrogate pairs", () => {
    // The leading "a" misaligns the pairs so the hard split lands mid-emoji.
    const text = `a${"😀".repeat(2050)}`;

    const chunks = splitMessage(text);

    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(TELEGRAM_MAX_MESSAGE_LENGTH);
      expect(chunk.isWellFormed()).toBe(true);
    }

    expect(chunks.join("")).toBe(text);
  });

  it("respects a custom limit", () => {
    expect(splitMessage("abc def", 3)).toEqual(["abc", " de", "f"]);
  });
});

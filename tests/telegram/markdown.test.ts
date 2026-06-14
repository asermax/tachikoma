import { describe, expect, it } from "vitest";

import { flattenTables, toTelegramMarkdown } from "../../src/extensions/telegram/markdown.ts";

describe("flattenTables", () => {
  it("flattens a standard two-column table to bolded-label bullets, dropping the header", () => {
    const input = [
      "| Day | Activities |",
      "|---|---|",
      "| Monday | Piano 7-8 PM |",
      "| Tuesday | Yoga 6 AM |",
    ].join("\n");

    expect(flattenTables(input)).toBe(
      ["- **Monday**: Piano 7-8 PM", "- **Tuesday**: Yoga 6 AM"].join("\n"),
    );
  });

  it("flattens a one-column table to plain bullets", () => {
    const input = ["| Item |", "|---|", "| Apple |", "| Banana |"].join("\n");

    expect(flattenTables(input)).toBe(["- Apple", "- Banana"].join("\n"));
  });

  it("joins the remaining columns of a three-column table with a middle dot", () => {
    const input = ["| A | B | C |", "|---|---|---|", "| 1 | 2 | 3 |", "| 4 | 5 | 6 |"].join("\n");

    expect(flattenTables(input)).toBe(["- **1**: 2 · 3", "- **4**: 5 · 6"].join("\n"));
  });

  it("renders without bolding when the first cell is empty", () => {
    const input = ["| Day | Note |", "|---|---|", "| | Empty day |"].join("\n");

    expect(flattenTables(input)).toBe("- Empty day");
  });

  it("preserves an empty later cell as an empty segment", () => {
    const input = ["| Day | Note |", "|---|---|", "| Monday | |"].join("\n");

    expect(flattenTables(input)).toBe("- **Monday**: ");
  });

  it("preserves inline markdown inside cells", () => {
    const input = ["| Name | Value |", "|---|---|", "| `code` | **bold** |"].join("\n");

    expect(flattenTables(input)).toBe("- **`code`**: **bold**");
  });

  it("handles tables written without leading/trailing border pipes", () => {
    const input = ["Day | Activities", "--- | ---", "Monday | Piano"].join("\n");

    expect(flattenTables(input)).toBe("- **Monday**: Piano");
  });

  it("keeps a header-only table's header text instead of dropping it", () => {
    const input = ["| Day | Activities |", "|---|---|"].join("\n");

    expect(flattenTables(input)).toBe("- **Day**: Activities");
  });

  it("does not flatten a table inside a backtick fenced code block", () => {
    const input = ["```", "| a | b |", "|---|---|", "| 1 | 2 |", "```"].join("\n");

    expect(flattenTables(input)).toBe(input);
  });

  it("does not flatten a table inside a tilde fence (with language hint)", () => {
    const input = ["~~~ts", "| a | b |", "|---|---|", "| 1 | 2 |", "~~~"].join("\n");

    expect(flattenTables(input)).toBe(input);
  });

  it("flattens a table at end of text with no trailing blank line", () => {
    const input = "Intro\n\n| a | b |\n|---|---|\n| 1 | 2 |";

    expect(flattenTables(input)).toBe("Intro\n\n- **1**: 2");
  });

  it("flattens each of two consecutive tables separated by a blank line", () => {
    const input = [
      "| a | b |",
      "|---|---|",
      "| 1 | 2 |",
      "",
      "| c | d |",
      "|---|---|",
      "| 3 | 4 |",
    ].join("\n");

    expect(flattenTables(input)).toBe(["- **1**: 2", "", "- **3**: 4"].join("\n"));
  });

  it("leaves pipe-bearing prose untouched when no separator row follows", () => {
    const input = "see foo | bar here";

    expect(flattenTables(input)).toBe(input);
  });

  it("leaves pipe-bearing prose untouched when a non-separator line follows", () => {
    const input = "see foo | bar\nthen more text";

    expect(flattenTables(input)).toBe(input);
  });

  it("is a no-op (byte-identical) for messages with no table", () => {
    const input = "Hello **world**\n\nThis is a paragraph with no table.\n- a list item";

    expect(flattenTables(input)).toBe(input);
  });
});

describe("toTelegramMarkdown with tables", () => {
  it("preserves surrounding formatting and escapes table cell content correctly (no double-escape)", () => {
    const input = [
      "No calendar conflicts next week. Here's what I'm thinking...",
      "",
      "| Day | Activities |",
      "|-----|-----------|",
      "| Monday | 🎹 Piano 7-8 PM |",
      "",
      "The main change is **Wednesday** getting trimmed down...",
    ].join("\n");

    const out = toTelegramMarkdown(input);

    // The table row became a bullet (flattened), so its text is present.
    expect(out).toContain("Monday");
    expect(out).toContain("Piano");
    // MarkdownV2 conversion happened on the surrounding text (periods escaped),
    // i.e. this is formatted output, not the plain-text fallback.
    expect(out).toContain("\\.");
    // The escape-mode table-cell double-escape bug is gone: "7-8" must not appear
    // as two literal backslashes before the hyphen.
    expect(out).not.toContain(String.raw`7\\-8`);
    // Surrounding bold survives conversion.
    expect(out.toLowerCase()).toContain("wednesday");
  });
});

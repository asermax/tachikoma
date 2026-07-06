import { describe, expect, it } from "vitest";

import { flattenTables } from "../../src/extensions/telegram/markdown.ts";

describe("flattenTables", () => {
  it("preserves the header as a bold first bullet, then bolded-label data rows", () => {
    const input = [
      "| Day | Activities |",
      "|---|---|",
      "| Monday | Piano 7-8 PM |",
      "| Tuesday | Yoga 6 AM |",
    ].join("\n");

    expect(flattenTables(input)).toBe(
      ["- **Day** · **Activities**", "- **Monday**: Piano 7-8 PM", "- **Tuesday**: Yoga 6 AM"].join(
        "\n",
      ),
    );
  });

  it("preserves the header row of a multi-column table (issue #407 reproduction)", () => {
    const input = [
      "| Provider | Model | $/article |",
      "|---|---|---|",
      "| Gemini | 3.1 Flash | $0.30 |",
      "| OpenAI | tts-1 | $0.11 |",
    ].join("\n");

    expect(flattenTables(input)).toBe(
      [
        "- **Provider** · **Model** · **$/article**",
        "- **Gemini**: 3.1 Flash · $0.30",
        "- **OpenAI**: tts-1 · $0.11",
      ].join("\n"),
    );
  });

  it("renders a one-column table's header bold and its data rows plain", () => {
    const input = ["| Item |", "|---|", "| Apple |", "| Banana |"].join("\n");

    expect(flattenTables(input)).toBe(["- **Item**", "- Apple", "- Banana"].join("\n"));
  });

  it("joins the header and remaining columns of a three-column table with a middle dot", () => {
    const input = ["| A | B | C |", "|---|---|---|", "| 1 | 2 | 3 |", "| 4 | 5 | 6 |"].join("\n");

    expect(flattenTables(input)).toBe(
      ["- **A** · **B** · **C**", "- **1**: 2 · 3", "- **4**: 5 · 6"].join("\n"),
    );
  });

  it("renders without bolding when the first data cell is empty", () => {
    const input = ["| Day | Note |", "|---|---|", "| | Empty day |"].join("\n");

    expect(flattenTables(input)).toBe(["- **Day** · **Note**", "- Empty day"].join("\n"));
  });

  it("preserves an empty later cell as an empty segment", () => {
    const input = ["| Day | Note |", "|---|---|", "| Monday | |"].join("\n");

    expect(flattenTables(input)).toBe(["- **Day** · **Note**", "- **Monday**: "].join("\n"));
  });

  it("preserves inline markdown inside data cells", () => {
    const input = ["| Name | Value |", "|---|---|", "| `code` | **bold** |"].join("\n");

    expect(flattenTables(input)).toBe(
      ["- **Name** · **Value**", "- **`code`**: **bold**"].join("\n"),
    );
  });

  it("preserves inline markdown inside header cells", () => {
    const input = ["| `Column A` | `Column B` |", "|---|---|", "| 1 | 2 |"].join("\n");

    expect(flattenTables(input)).toBe(
      ["- **`Column A`** · **`Column B`**", "- **1**: 2"].join("\n"),
    );
  });

  it("leaves an empty header cell un-bolded so no broken markup is emitted", () => {
    const input = ["| A |  | C |", "|---|---|---|", "| 1 | 2 | 3 |"].join("\n");

    const output = flattenTables(input);

    expect(output).not.toContain("****");
    expect(output).toBe(["- **A** ·  · **C**", "- **1**: 2 · 3"].join("\n"));
  });

  it("handles tables written without leading/trailing border pipes", () => {
    const input = ["Day | Activities", "--- | ---", "Monday | Piano"].join("\n");

    expect(flattenTables(input)).toBe(
      ["- **Day** · **Activities**", "- **Monday**: Piano"].join("\n"),
    );
  });

  it("renders a header-only table's header as a bold header bullet", () => {
    const input = ["| Day | Activities |", "|---|---|"].join("\n");

    expect(flattenTables(input)).toBe("- **Day** · **Activities**");
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

    expect(flattenTables(input)).toBe("Intro\n\n- **a** · **b**\n- **1**: 2");
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

    expect(flattenTables(input)).toBe(
      ["- **a** · **b**", "- **1**: 2", "", "- **c** · **d**", "- **3**: 4"].join("\n"),
    );
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

import { describe, expect, it } from "vitest";

import {
  type ButtonSpec,
  buildMultiSelectKeyboard,
  doneButtonLabel,
  unpackCallbackData,
} from "../../src/extensions/telegram/buttons.ts";
import { MultiSelectRegistry } from "../../src/extensions/telegram/multiselect.ts";

const rows: ButtonSpec[][] = [
  [
    { label: "Yes", value: "yes" },
    { label: "No", value: "no" },
  ],
  [{ label: "Maybe", value: "maybe" }],
];

describe("multi-select callback data", () => {
  it("unpacks toggles and done controls", () => {
    expect(unpackCallbackData("btnM:yes")).toEqual({ kind: "toggle", value: "yes" });
    expect(unpackCallbackData("btnD:")).toEqual({ kind: "done" });
  });

  it("keeps unpacking single-select taps and rejecting unknown prefixes", () => {
    expect(unpackCallbackData("btn1:yes")).toEqual({
      kind: "tap",
      value: "yes",
      singleUse: true,
    });
    expect(unpackCallbackData("btnN:no")).toEqual({
      kind: "tap",
      value: "no",
      singleUse: false,
    });
    expect(unpackCallbackData("other:yes")).toBeNull();
  });
});

describe("buildMultiSelectKeyboard", () => {
  it("marks selected options and appends the Done control with the live count", () => {
    expect(buildMultiSelectKeyboard(rows, new Set(["yes", "maybe"]))).toEqual({
      inline_keyboard: [
        [
          { text: "✅ Yes", callback_data: "btnM:yes" },
          { text: "No", callback_data: "btnM:no" },
        ],
        [{ text: "✅ Maybe", callback_data: "btnM:maybe" }],
        [{ text: "Done (2)", callback_data: "btnD:" }],
      ],
    });
  });

  it("renders the fresh keyboard (nothing selected) with a bare Done label", () => {
    expect(buildMultiSelectKeyboard(rows, new Set())).toEqual({
      inline_keyboard: [
        [
          { text: "Yes", callback_data: "btnM:yes" },
          { text: "No", callback_data: "btnM:no" },
        ],
        [{ text: "Maybe", callback_data: "btnM:maybe" }],
        [{ text: "Done", callback_data: "btnD:" }],
      ],
    });
    expect(doneButtonLabel(0)).toBe("Done");
    expect(doneButtonLabel(3)).toBe("Done (3)");
  });
});

describe("MultiSelectRegistry", () => {
  it("toggles values and returns the live state", () => {
    const registry = new MultiSelectRegistry();
    registry.seed(1, rows);

    const state = registry.toggle(1, "yes");
    expect([...(state?.selected ?? [])]).toEqual(["yes"]);

    // The live entry, not a snapshot: toggling back off is visible through the same reference.
    registry.toggle(1, "yes");
    expect(state?.selected.has("yes")).toBe(false);
  });

  it("no-ops a toggle for a value that is not one of the prompt's options", () => {
    const registry = new MultiSelectRegistry();
    registry.seed(1, rows);

    const state = registry.toggle(1, "unknown");
    expect(state?.selected.size).toBe(0);
  });

  it("returns null for a message with no registered state", () => {
    expect(new MultiSelectRegistry().toggle(404, "yes")).toBeNull();
  });

  it("submits selected values in displayed order and clears the state", () => {
    const registry = new MultiSelectRegistry();
    registry.seed(1, rows);
    registry.toggle(1, "maybe");
    registry.toggle(1, "yes");

    expect(registry.finish(1)).toEqual({ status: "submitted", values: ["yes", "maybe"] });
    expect(registry.finish(1)).toEqual({ status: "duplicate" });
  });

  it("reports an unknown finish and claims the message as finished", () => {
    const registry = new MultiSelectRegistry();

    expect(registry.finish(404)).toEqual({ status: "unknown" });
    expect(registry.isFinished(404)).toBe(true);
    expect(registry.finish(404)).toEqual({ status: "duplicate" });
  });

  it("claims degraded messages exactly once via markFinished", () => {
    const registry = new MultiSelectRegistry();

    expect(registry.markFinished(7)).toBe(true);
    expect(registry.markFinished(7)).toBe(false);
  });

  it("evicts the oldest never-submitted prompt beyond the cap", () => {
    const registry = new MultiSelectRegistry();

    for (let id = 1; id <= 26; id += 1) registry.seed(id, rows);

    expect(registry.toggle(1, "yes")).toBeNull();
    expect(registry.toggle(26, "yes")?.selected.has("yes")).toBe(true);
  });

  it("bounds the finished set the same way", () => {
    const registry = new MultiSelectRegistry();

    for (let id = 1; id <= 26; id += 1) registry.markFinished(id);

    expect(registry.isFinished(1)).toBe(false);
    expect(registry.isFinished(26)).toBe(true);
  });
});

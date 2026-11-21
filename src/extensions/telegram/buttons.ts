import type { InlineKeyboardMarkup } from "grammy/types";

// Telegram caps callback_data at 64 bytes; the 5-byte prefix leaves 58 for the value.
export const MAX_BUTTON_VALUE_BYTES = 58;
export const MAX_BUTTON_COUNT = 100;
/** Multi-select options reserve one of Telegram's 100 button slots for the Done control. */
export const MAX_MULTI_SELECT_OPTIONS = MAX_BUTTON_COUNT - 1;

const SINGLE_USE_PREFIX = "btn1:";
const MULTI_USE_PREFIX = "btnN:";
const MULTI_SELECT_PREFIX = "btnM:";
const DONE_PREFIX = "btnD:";

/** Prepended to a selected option's label when a multi-select keyboard re-renders. */
export const SELECTED_MARKER = "✅ ";

export interface ButtonSpec {
  label: string;
  value: string;
}

export const packCallbackData = (value: string, singleUse: boolean): string =>
  `${singleUse ? SINGLE_USE_PREFIX : MULTI_USE_PREFIX}${value}`;

/** Unpacked callback data: a single-select tap, a multi-select option toggle, or a multi-select Done. */
export type CallbackData =
  | { kind: "tap"; value: string; singleUse: boolean }
  | { kind: "toggle"; value: string }
  | { kind: "done" };

export const unpackCallbackData = (data: string): CallbackData | null => {
  if (data.startsWith(SINGLE_USE_PREFIX)) {
    return { kind: "tap", value: data.slice(SINGLE_USE_PREFIX.length), singleUse: true };
  }

  if (data.startsWith(MULTI_USE_PREFIX)) {
    return { kind: "tap", value: data.slice(MULTI_USE_PREFIX.length), singleUse: false };
  }

  if (data.startsWith(MULTI_SELECT_PREFIX)) {
    return { kind: "toggle", value: data.slice(MULTI_SELECT_PREFIX.length) };
  }

  if (data.startsWith(DONE_PREFIX)) {
    return { kind: "done" };
  }

  return null;
};

export interface ValidateButtonsOptions {
  /** Multi-select mode: the Done control takes a button slot, and the selection marker is reserved. */
  multiSelect?: boolean;
}

export const validateButtons = (rows: ButtonSpec[][], options?: ValidateButtonsOptions): void => {
  if (rows.length === 0) throw new Error("buttons must contain at least one row");

  const multiSelect = options?.multiSelect === true;
  let total = 0;

  rows.forEach((row, rowIndex) => {
    if (row.length === 0) {
      throw new Error(`row ${rowIndex} must contain at least one button`);
    }

    row.forEach((button, buttonIndex) => {
      if (button.label.trim().length === 0) {
        throw new Error(`button at row ${rowIndex} index ${buttonIndex} has an empty label`);
      }

      if (multiSelect && button.label.startsWith(SELECTED_MARKER)) {
        throw new Error(
          `button at row ${rowIndex} index ${buttonIndex} has a label starting with ` +
            `"${SELECTED_MARKER.trim()}" — that marker is reserved for selected options in a multi-select`,
        );
      }

      if (button.value.length === 0) {
        throw new Error(`button at row ${rowIndex} index ${buttonIndex} has an empty value`);
      }

      const bytes = Buffer.byteLength(button.value, "utf8");
      if (bytes > MAX_BUTTON_VALUE_BYTES) {
        throw new Error(
          `button value "${button.value.slice(0, 20)}…" exceeds the ` +
            `${MAX_BUTTON_VALUE_BYTES}-byte limit (${bytes} bytes)`,
        );
      }

      total += 1;
    });
  });

  const cap = multiSelect ? MAX_MULTI_SELECT_OPTIONS : MAX_BUTTON_COUNT;
  if (total > cap) {
    const detail = multiSelect
      ? ` — the Done control takes the final slot of Telegram's ${MAX_BUTTON_COUNT} buttons`
      : "";
    throw new Error(`total button count (${total}) exceeds the cap (${cap})${detail}`);
  }
};

/** The Done control's label; the live count is the only selection feedback besides the ✅ markers. */
export const doneButtonLabel = (count: number): string => (count > 0 ? `Done (${count})` : "Done");

/**
 * A multi-select keyboard: the agent's option rows in their given order (selected values labeled
 * with the marker) plus the appended Done control whose tap submits the whole selection. Built
 * fresh from the registry's live state on every re-render.
 */
export const buildMultiSelectKeyboard = (
  rows: ButtonSpec[][],
  selected: ReadonlySet<string>,
): InlineKeyboardMarkup => ({
  inline_keyboard: [
    ...rows.map((row) =>
      row.map((button) => ({
        text: selected.has(button.value) ? `${SELECTED_MARKER}${button.label}` : button.label,
        callback_data: `${MULTI_SELECT_PREFIX}${button.value}`,
      })),
    ),
    [{ text: doneButtonLabel(selected.size), callback_data: DONE_PREFIX }],
  ],
});

export const buildInlineKeyboard = (
  rows: ButtonSpec[][],
  singleUse: boolean,
): InlineKeyboardMarkup => ({
  inline_keyboard: rows.map((row) =>
    row.map((button) => ({
      text: button.label,
      callback_data: packCallbackData(button.value, singleUse),
    })),
  ),
});

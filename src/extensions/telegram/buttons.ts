import type { InlineKeyboardMarkup } from "grammy/types";

// Telegram caps callback_data at 64 bytes; the 5-byte prefix leaves 58 for the value.
export const MAX_BUTTON_VALUE_BYTES = 58;
export const MAX_BUTTON_COUNT = 100;

const SINGLE_USE_PREFIX = "btn1:";
const MULTI_USE_PREFIX = "btnN:";

export interface ButtonSpec {
  label: string;
  value: string;
}

export const packCallbackData = (value: string, singleUse: boolean): string =>
  `${singleUse ? SINGLE_USE_PREFIX : MULTI_USE_PREFIX}${value}`;

export const unpackCallbackData = (data: string): { value: string; singleUse: boolean } | null => {
  if (data.startsWith(SINGLE_USE_PREFIX)) {
    return { value: data.slice(SINGLE_USE_PREFIX.length), singleUse: true };
  }

  if (data.startsWith(MULTI_USE_PREFIX)) {
    return { value: data.slice(MULTI_USE_PREFIX.length), singleUse: false };
  }

  return null;
};

export const validateButtons = (rows: ButtonSpec[][]): void => {
  if (rows.length === 0) throw new Error("buttons must contain at least one row");

  let total = 0;

  rows.forEach((row, rowIndex) => {
    if (row.length === 0) {
      throw new Error(`row ${rowIndex} must contain at least one button`);
    }

    row.forEach((button, buttonIndex) => {
      if (button.label.trim().length === 0) {
        throw new Error(`button at row ${rowIndex} index ${buttonIndex} has an empty label`);
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

  if (total > MAX_BUTTON_COUNT) {
    throw new Error(`total button count (${total}) exceeds the cap (${MAX_BUTTON_COUNT})`);
  }
};

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

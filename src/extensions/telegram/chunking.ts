// Telegram's hard limit, in UTF-16 code units — exactly what JS string length measures.
export const TELEGRAM_MAX_MESSAGE_LENGTH = 4096;

/**
 * Split text into chunks within Telegram's message length limit, preferring
 * paragraph boundaries, then line boundaries, then a hard split that never
 * cuts a surrogate pair in half.
 */
export const splitMessage = (text: string, limit = TELEGRAM_MAX_MESSAGE_LENGTH): string[] => {
  if (text.length <= limit) return [text];

  const chunks: string[] = [];
  let current = "";

  for (const paragraph of text.split("\n\n")) {
    for (const piece of splitParagraph(paragraph, limit)) {
      const joined = current.length > 0 ? `${current}\n\n${piece}` : piece;

      if (joined.length <= limit) {
        current = joined;
      } else {
        if (current.length > 0) chunks.push(current);
        current = piece;
      }
    }
  }

  if (current.length > 0) chunks.push(current);

  return chunks;
};

const splitParagraph = (paragraph: string, limit: number): string[] => {
  if (paragraph.length <= limit) return [paragraph];

  const pieces: string[] = [];
  let current = "";

  for (const line of paragraph.split("\n")) {
    for (const fragment of hardSplit(line, limit)) {
      const joined = current.length > 0 ? `${current}\n${fragment}` : fragment;

      if (joined.length <= limit) {
        current = joined;
      } else {
        if (current.length > 0) pieces.push(current);
        current = fragment;
      }
    }
  }

  if (current.length > 0) pieces.push(current);

  return pieces;
};

const hardSplit = (line: string, limit: number): string[] => {
  if (line.length <= limit) return [line];

  const fragments: string[] = [];
  let start = 0;

  while (start < line.length) {
    let end = Math.min(start + limit, line.length);

    if (end < line.length && isLowSurrogate(line.charCodeAt(end))) end -= 1;

    fragments.push(line.slice(start, end));
    start = end;
  }

  return fragments;
};

const isLowSurrogate = (code: number): boolean => code >= 0xdc00 && code <= 0xdfff;

import { open, readFile, stat } from "node:fs/promises";

// Generous raw window — truncateTail trims it to pi's byte/line limits afterwards.
const TAIL_READ_BYTES = 256 * 1024;

/** Read the last chunk of a log file; null when the file does not exist. */
export const readOutputTail = async (path: string): Promise<string | null> => {
  let size: number;

  try {
    size = (await stat(path)).size;
  } catch {
    return null;
  }

  if (size === 0) return "";

  const start = Math.max(0, size - TAIL_READ_BYTES);
  const handle = await open(path, "r");

  try {
    const buffer = Buffer.alloc(size - start);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, start);

    return buffer.subarray(0, bytesRead).toString("utf-8");
  } finally {
    await handle.close();
  }
};

export interface OutputWindow {
  content: string;
  /** Total number of lines in the log. */
  totalLines: number;
  /** True when the requested offset begins at or after EOF. */
  pastEnd: boolean;
}

/**
 * Read a `[offset, offset + count)` window of lines from a log file; null when
 * the file does not exist. Offsets are 0-based. A window starting past the last
 * line yields empty content with `pastEnd` set.
 */
export const readOutputWindow = async (
  path: string,
  offset: number,
  count: number,
): Promise<OutputWindow | null> => {
  let raw: string;

  try {
    raw = await readFile(path, "utf-8");
  } catch {
    return null;
  }

  if (raw === "") return { content: "", totalLines: 0, pastEnd: offset > 0 };

  // A trailing newline is a line terminator, not an extra empty line.
  const lines = (raw.endsWith("\n") ? raw.slice(0, -1) : raw).split("\n");

  return {
    content: lines.slice(offset, offset + count).join("\n"),
    totalLines: lines.length,
    pastEnd: offset >= lines.length,
  };
};

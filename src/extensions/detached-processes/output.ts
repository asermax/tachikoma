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

/** A log stream to merge: a human label and the path to its log file. */
export interface StreamedLog {
  label: string;
  path: string;
}

/** Render non-empty `{label, content}` pairs as `[label]\n<content>` sections, blank-line separated. */
const formatSections = (parts: { label: string; content: string }[]): string =>
  parts.map((part) => `[${part.label}]\n${part.content.replace(/\n+$/, "")}`).join("\n\n");

/**
 * Read the tail of several log streams and present each non-empty one as a labeled
 * section (`[label]\n<content>`), joined by a blank line. Returns null when no
 * stream has content. Merges stdout+stderr so a caller doesn't have to know which
 * stream a process writes to.
 */
export const readOutputTailMerged = async (streams: StreamedLog[]): Promise<string | null> => {
  const parts: { label: string; content: string }[] = [];

  for (const { label, path } of streams) {
    const tail = await readOutputTail(path);
    if (tail != null && tail !== "") parts.push({ label, content: tail });
  }

  return parts.length === 0 ? null : formatSections(parts);
};

export interface MergedOutputWindow {
  /** Separated labeled sections, or "" when no stream had lines in the window. */
  content: string;
  /** True when no stream yielded window content but at least one stream has lines. */
  pastEnd: boolean;
  /** Largest totalLines across the streams — 0 when every stream is empty (no lines at all). */
  totalLines: number;
}

/**
 * Apply the same `[offset, offset + count)` line window to each stream and present
 * the non-empty results as separated labeled sections (parallel paging). A stream
 * whose window is past EOF is omitted. `totalLines` is the longest stream's line
 * count, for the past-EOF message.
 */
export const readOutputWindowMerged = async (
  streams: StreamedLog[],
  offset: number,
  count: number,
): Promise<MergedOutputWindow> => {
  const parts: { label: string; content: string }[] = [];
  let totalLines = 0;

  for (const { label, path } of streams) {
    const window = await readOutputWindow(path, offset, count);
    if (window == null) continue;

    totalLines = Math.max(totalLines, window.totalLines);

    if (!window.pastEnd && window.content !== "") parts.push({ label, content: window.content });
  }

  return {
    content: formatSections(parts),
    // `totalLines` is 0 iff no stream has any lines, i.e. everything is empty — so
    // "past end" only applies when at least one stream has lines but none are in range.
    pastEnd: parts.length === 0 && totalLines > 0,
    totalLines,
  };
};

import { open, stat } from "node:fs/promises";

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

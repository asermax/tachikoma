import { compareQueued, type QueuedItem } from "./delivery-queue.ts";

const PREAMBLE =
  "Background updates arrived while you were busy. Relay or act on each as appropriate.";

/**
 * Combine queued items into one prompt the agent surfaces as a single turn, tier/FIFO
 * ordered. Each item's text already carries its intent (a notice reads as an update, a
 * scheduled task reads as an instruction).
 */
export const buildDigest = (items: QueuedItem[]): string => {
  const lines = [...items].sort(compareQueued).map((item) => `- ${item.text}`);

  return `<queued-notifications>\n${PREAMBLE}\n${lines.join("\n")}\n</queued-notifications>`;
};

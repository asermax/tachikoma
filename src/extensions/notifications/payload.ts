// The notify event contract is defined in neutral `src/events.ts` so cross-extension
// emitters never import from this extension's directory (DES-002); everything below
// re-exports it, keeping existing importers unchanged. Only the lenient parsing of
// the payloads this extension routes stays here.

export type { NotifyPayload, Severity } from "../../events.ts";
export { NOTIFY_EVENT, SEVERITIES } from "../../events.ts";

import { type NotifyPayload, SEVERITIES, type Severity } from "../../events.ts";

const isSeverity = (value: unknown): value is Severity =>
  typeof value === "string" && value in SEVERITIES;

/**
 * Parse an arbitrary `"notify"` event payload into a NotifyPayload.
 *
 * Requires a non-empty `text` string; payloads without one are not notifications
 * and return null. Missing or unknown severity downgrades to "info", missing
 * source becomes "unknown" — cross-extension signals stay best-effort.
 */
export const parseNotifyPayload = (payload: unknown): NotifyPayload | null => {
  if (payload == null || typeof payload !== "object") return null;

  const candidate = payload as Record<string, unknown>;

  if (typeof candidate.text !== "string" || candidate.text.trim() === "") return null;

  return {
    title: typeof candidate.title === "string" ? candidate.title : undefined,
    text: candidate.text,
    severity: isSeverity(candidate.severity) ? candidate.severity : SEVERITIES.info,
    source: typeof candidate.source === "string" ? candidate.source : "unknown",
  };
};

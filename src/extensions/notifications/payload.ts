/** App event channel for user-facing notifications from any extension. */
export const NOTIFY_EVENT = "notify";

export const SEVERITIES = {
  info: "info",
  warning: "warning",
  urgent: "urgent",
} as const;

export type Severity = keyof typeof SEVERITIES;

export interface NotifyPayload {
  title?: string;
  text: string;
  severity: Severity;
  source: string;
}

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

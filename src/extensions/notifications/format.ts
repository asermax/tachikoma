import { formatTimestamp } from "../../util/dates.ts";
import type { NotifyPayload } from "./payload.ts";

/** Single notification with the source/time prefix block. */
export const formatNotification = (
  payload: NotifyPayload,
  now: Date,
  timezone?: string,
): string => {
  const body = payload.title != null ? `${payload.title}\n\n${payload.text}` : payload.text;

  return `--- Notification ---\nSource: ${payload.source}\nTime: ${formatTimestamp(now, timezone)}\n\n${body}`;
};

/** Combined digest for several accumulated notices. */
export const formatDigest = (items: NotifyPayload[], now: Date, timezone?: string): string => {
  const parts = [`--- Notifications digest ---\nTime: ${formatTimestamp(now, timezone)}`, ""];

  items.forEach((item, index) => {
    parts.push(`— Item ${index + 1} (${item.severity}, source: ${item.source}) —`);
    parts.push(item.title != null ? `${item.title}\n${item.text}` : item.text);
    parts.push("");
  });

  return parts.join("\n");
};

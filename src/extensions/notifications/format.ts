import type { NotifyPayload } from "./payload.ts";

const formatTimestamp = (date: Date): string =>
  `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;

/** Single notification with the source/time prefix block (ported from Python notifications.py). */
export const formatNotification = (payload: NotifyPayload, now: Date): string => {
  const body = payload.title != null ? `${payload.title}\n\n${payload.text}` : payload.text;

  return `--- Notification ---\nSource: ${payload.source}\nTime: ${formatTimestamp(now)}\n\n${body}`;
};

/** Combined digest for several accumulated notices (ported from Python buffer/digest.py). */
export const formatDigest = (items: NotifyPayload[], now: Date): string => {
  const parts = [`--- Notifications digest ---\nTime: ${formatTimestamp(now)}`, ""];

  items.forEach((item, index) => {
    parts.push(`— Item ${index + 1} (${item.severity}, source: ${item.source}) —`);
    parts.push(item.title != null ? `${item.title}\n${item.text}` : item.text);
    parts.push("");
  });

  return parts.join("\n");
};

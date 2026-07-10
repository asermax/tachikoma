/**
 * Timezone-aware date helpers for user-facing surfaces. Every formatter honors an
 * optional IANA `timezone` (the configured `config.scheduler.timezone`); omitting it
 * falls back to the process timezone, preserving the prior behavior for callers that
 * don't thread the configured zone.
 */

/**
 * Local calendar date (`YYYY-MM-DD`) for an instant in a given IANA timezone.
 * `en-CA` formats dates ISO-style, so it yields the day key directly. This is the
 * single owner of that format; the trunk logic's `localDay` delegates here.
 * Date-stamped artifacts follow the user's day, not UTC.
 */
export const localIsoDate = (date: Date = new Date(), timezone?: string): string =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);

/**
 * Human-readable timestamp `YYYY-MM-DD HH:mm <zone>` in a given IANA timezone, for
 * user-facing surfaces (notifications, process status). Assembled from explicit parts
 * so field order and precision stay stable across locales (unlike `toLocaleString`,
 * where zone placement varies), keeping output deterministic and testable.
 */
export const formatTimestamp = (date: Date, timezone?: string): string => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    // h23 avoids Node's "24:00" midnight pitfall under hour12: false.
    hourCycle: "h23",
    timeZoneName: "short",
  }).formatToParts(date);
  const get = (type: string): string => parts.find((p) => p.type === type)?.value ?? "";

  // trimEnd() drops the trailing space if a future ICU build omits timeZoneName.
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} ${get(
    "timeZoneName",
  )}`.trimEnd();
};

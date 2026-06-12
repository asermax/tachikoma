import { Cron } from "croner";

import type { StoredSchedule } from "./schema.ts";

export class ScheduleError extends Error {}

/**
 * Parse a schedule string into its stored form: a cron expression (recurring)
 * or an ISO datetime (one-shot). Bare datetimes are interpreted in the
 * configured timezone; explicit offsets (including Z) are preserved.
 */
export const parseSchedule = (
  raw: string,
  timezone: string | undefined,
  now: Date,
): StoredSchedule => {
  let probe: Cron;

  try {
    probe = new Cron(raw, { timezone });
  } catch {
    throw new ScheduleError(
      `Invalid schedule '${raw}'. Use a cron expression (e.g., '0 9 * * *') or an ISO datetime (e.g., '2026-03-22T10:00:00Z').`,
    );
  }

  // getPattern() is undefined when croner parsed the input as a one-shot datetime.
  if (probe.getPattern() != null) return { type: "cron", expression: raw };

  const at = probe.nextRun(now);

  if (at == null) {
    throw new ScheduleError(`One-shot schedule datetime must be in the future. Got: ${raw}`);
  }

  return { type: "once", at: at.toISOString() };
};

/** Next cron occurrence strictly after `anchor`, or null when none exists. */
export const nextCronRun = (
  expression: string,
  timezone: string | undefined,
  anchor: Date,
): Date | null => new Cron(expression, { timezone }).nextRun(anchor);

export const formatInTimezone = (date: Date, timezone: string | undefined): string =>
  date.toLocaleString("sv-SE", { timeZone: timezone, timeZoneName: "short" });

export const formatSchedule = (schedule: StoredSchedule, timezone: string | undefined): string =>
  schedule.type === "cron"
    ? `cron: ${schedule.expression}`
    : `once: ${formatInTimezone(new Date(schedule.at), timezone)}`;

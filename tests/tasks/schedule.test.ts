import { Cron } from "croner";
import { describe, expect, it } from "vitest";

import { nextCronRun, parseSchedule, ScheduleError } from "../../src/extensions/tasks/schedule.ts";

// Pins of croner 11 at the two seams that hand a schedule to croner (S5, R6). Every value was
// observed on croner 11.0.1.
//
// New York has DST. On 2026-03-08 its clock jumps from 02:00 to 03:00, so 02:30 does not exist
// that day. On 2026-11-01 it falls back from 02:00 to 01:00, so 01:00 to 01:59 occurs twice.
// Buenos Aires has had no DST since 2009 and stays at UTC-3.
//
// croner 11 sorts every pattern into one of two kinds, which handle a transition differently. A
// fixed-time job has one value, and no wildcard or step, in each of seconds, minutes and hours,
// such as `30 2 * * *`. Every other pattern is an interval job, `0 * * * *` included.
const NEW_YORK = "America/New_York";
const BUENOS_AIRES = "America/Argentina/Buenos_Aires";

const NOW = new Date("2026-06-01T12:00:00Z");

const iso = (date: Date): string => date.toISOString();

// The wall clock of an instant in a timezone, as "HH:mm".
const wallClock = (date: Date, timezone: string): string => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hourCycle: "h23",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(date);
  const field = (type: Intl.DateTimeFormatPartTypes): string | undefined =>
    parts.find((part) => part.type === type)?.value;

  return `${field("hour")}:${field("minute")}`;
};

// What parseSchedule throws for a schedule, or null when it accepts the schedule.
const rejection = (raw: string): Error | null => {
  try {
    parseSchedule(raw, NEW_YORK, NOW);
    return null;
  } catch (error) {
    return error instanceof Error ? error : new Error(String(error));
  }
};

describe("a schedule croner rejects", () => {
  // A number with something after it, junk after a day-of-month modifier, a name glued to a
  // digit, a step on a name, the wrong number of fields, and a value out of range.
  //
  // croner 10.0.1 rejected all of these except two. It read `3*` as 3, and it read `SUN6` in the
  // day-of-week field as day 6, so `0 0 * * SUN6` ran every Saturday at midnight. croner 11 requires
  // a number to be digits only and a name to stand alone. A definition stored with such an
  // expression before the upgrade is what tests/tasks/generation.test.ts skips on the tick.
  it.each([
    "3* * * * *",
    "15W5 * * * *",
    "SUN6 * * * *",
    "JANFEB * * * *",
    "*/FRI * * * *",
    "not a cron",
    "99 * * * *",
    "0 0 * * SUN6",
  ])("rejects %j as an invalid schedule", (raw) => {
    const error = rejection(raw);

    expect(error).toBeInstanceOf(ScheduleError);
    expect(error?.message.startsWith(`Invalid schedule '${raw}'.`)).toBe(true);
  });
});

describe("an accepted schedule", () => {
  it.each(["0 9 * * *", "*/5 * * * *"])("stores %j as a cron expression", (raw) => {
    expect(parseSchedule(raw, NEW_YORK, NOW)).toEqual({ type: "cron", expression: raw });
  });

  it("stores a datetime as a one-shot at that instant", () => {
    expect(parseSchedule("2030-01-01T00:00:00Z", NEW_YORK, NOW)).toEqual({
      type: "once",
      at: "2030-01-01T00:00:00.000Z",
    });
  });

  it("rejects a datetime in the past", () => {
    const error = rejection("2020-01-01T00:00:00Z");

    expect(error).toBeInstanceOf(ScheduleError);
    expect(error?.message).toBe(
      "One-shot schedule datetime must be in the future. Got: 2020-01-01T00:00:00Z",
    );
  });
});

describe("a cron expression that can never match", () => {
  // No February has a 30th day.
  it("has a next run of null, not an error", () => {
    expect(nextCronRun("0 0 30 2 *", NEW_YORK, NOW)).toBeNull();
    expect(new Cron("0 0 30 2 *", { timezone: NEW_YORK }).nextRuns(3, NOW)).toEqual([]);
  });

  it("parses and stores like any other cron expression", () => {
    expect(parseSchedule("0 0 30 2 *", NEW_YORK, NOW)).toEqual({
      type: "cron",
      expression: "0 0 30 2 *",
    });
  });
});

describe("the next run outside a DST transition", () => {
  // The anchor is 09:00 on the dot in Buenos Aires and 08:00 in New York. A run at the anchor
  // instant itself is not returned, so the 09:00 job waits for the next day there.
  it.each([
    ["0 9 * * *", BUENOS_AIRES, "2026-06-02T12:00:00Z", "09:00"],
    ["0 9 * * *", NEW_YORK, "2026-06-01T13:00:00Z", "09:00"],
    ["*/15 * * * *", BUENOS_AIRES, "2026-06-01T12:15:00Z", "09:15"],
    ["*/15 * * * *", NEW_YORK, "2026-06-01T12:15:00Z", "08:15"],
    ["0 0 * * 1", BUENOS_AIRES, "2026-06-08T03:00:00Z", "00:00"],
    ["0 0 * * 1", NEW_YORK, "2026-06-08T04:00:00Z", "00:00"],
    ["30 2 14 * *", BUENOS_AIRES, "2026-06-14T05:30:00Z", "02:30"],
    ["30 2 14 * *", NEW_YORK, "2026-06-14T06:30:00Z", "02:30"],
  ])("computes the next run of %j in %s", (expression, timezone, expected, local) => {
    const next = nextCronRun(expression, timezone, NOW);

    expect(next?.toISOString()).toBe(new Date(expected).toISOString());
    expect(next && wallClock(next, timezone)).toBe(local);
  });
});

describe("the next run across a spring-forward gap", () => {
  // Midnight before the gap, in New York.
  const from = new Date("2026-03-08T00:00:00-05:00");

  it("puts a fixed-time job at the first valid time after the gap", () => {
    const job = new Cron("30 2 * * *", { timezone: NEW_YORK });

    // 02:30 does not exist on this day. croner 11 runs the job at 03:00, when the clock has
    // jumped. croner 10.0.1 ran it at 03:30.
    expect(nextCronRun("30 2 * * *", NEW_YORK, from)).toEqual(new Date("2026-03-08T07:00:00.000Z"));

    // The next day the job is back on its own wall time.
    const runs = job.nextRuns(2, from);

    expect(runs.map(iso)).toEqual(["2026-03-08T07:00:00.000Z", "2026-03-09T06:30:00.000Z"]);
    expect(runs.map((run) => wallClock(run, NEW_YORK))).toEqual(["03:00", "02:30"]);
  });

  // An interval job is handled the other way round, and Tachikoma adopts that as it is. It is the
  // rule of Vixie cron, whose crontab(5) says that a time which does not exist never matches. The
  // croner decision of this delta takes croner 11 with a pin for each of its scheduling changes
  // and no production change, and these two cases are that pin for the gap.
  it("skips an interval job for the whole day when all of its times are in the gap", () => {
    const job = new Cron("*/30 2 * * *", { timezone: NEW_YORK });

    // 02:00 and 02:30 do not exist on 2026-03-08, so the job does not run that day. croner 10.0.1
    // gave 03:00 and 03:30 instead.
    expect(nextCronRun("*/30 2 * * *", NEW_YORK, from)).toEqual(
      new Date("2026-03-09T06:00:00.000Z"),
    );

    // The next day it is back on its own wall time.
    const runs = job.nextRuns(2, from);

    expect(runs.map(iso)).toEqual(["2026-03-09T06:00:00.000Z", "2026-03-09T06:30:00.000Z"]);
    expect(runs.map((run) => wallClock(run, NEW_YORK))).toEqual(["02:00", "02:30"]);
  });

  it("skips only the times in the gap when the job has times outside it too", () => {
    const job = new Cron("*/30 1-3 * * *", { timezone: NEW_YORK });

    // 01:00, 01:30, 03:00 and 03:30 exist and run. 02:00 and 02:30 do not, and are skipped, where
    // croner 10.0.1 moved them to 03:00 and 03:30, times the job already has. So the day has these
    // four runs, and the fifth is 01:00 the next day.
    const runs = job.nextRuns(5, from);

    expect(runs.map(iso)).toEqual([
      "2026-03-08T06:00:00.000Z",
      "2026-03-08T06:30:00.000Z",
      "2026-03-08T07:00:00.000Z",
      "2026-03-08T07:30:00.000Z",
      "2026-03-09T05:00:00.000Z",
    ]);
    expect(runs.map((run) => wallClock(run, NEW_YORK))).toEqual([
      "01:00",
      "01:30",
      "03:00",
      "03:30",
      "01:00",
    ]);
  });
});

describe("the next run across a fall-back overlap", () => {
  // 01:30 on the first pass of the repeated hour, in New York. The clock falls back at 02:00
  // daylight time, so 01:00 to 01:59 comes twice: as 05:00Z to 05:59Z, then as 06:00Z to 06:59Z.
  const insideOverlap = new Date("2026-11-01T01:30:00-04:00");
  // 00:59, before the first pass.
  const beforeOverlap = new Date("2026-11-01T00:59:00-04:00");

  it("runs an interval job on both occurrences of the repeated hour", () => {
    const job = new Cron("0 * * * *", { timezone: NEW_YORK });

    // 01:00 on its first pass and again on its second.
    const acrossOverlap = job.nextRuns(2, beforeOverlap);

    expect(acrossOverlap.map(iso)).toEqual([
      "2026-11-01T05:00:00.000Z",
      "2026-11-01T06:00:00.000Z",
    ]);
    expect(acrossOverlap.map((run) => wallClock(run, NEW_YORK))).toEqual(["01:00", "01:00"]);

    // From inside the overlap, the next run is the second occurrence of an hour that already ran.
    const fromInside = job.nextRuns(4, insideOverlap);

    expect(fromInside.map(iso)).toEqual([
      "2026-11-01T06:00:00.000Z",
      "2026-11-01T07:00:00.000Z",
      "2026-11-01T08:00:00.000Z",
      "2026-11-01T09:00:00.000Z",
    ]);
    expect(fromInside.map((run) => wallClock(run, NEW_YORK))).toEqual([
      "01:00",
      "02:00",
      "03:00",
      "04:00",
    ]);
  });

  it("runs a fixed-time job once in the overlap, on its first occurrence", () => {
    const job = new Cron("30 1 * * *", { timezone: NEW_YORK });

    // 01:30 on the first pass, then not again an hour later on the second.
    const acrossOverlap = job.nextRuns(2, beforeOverlap);

    expect(acrossOverlap.map(iso)).toEqual([
      "2026-11-01T05:30:00.000Z",
      "2026-11-02T06:30:00.000Z",
    ]);
    expect(acrossOverlap.map((run) => wallClock(run, NEW_YORK))).toEqual(["01:30", "01:30"]);

    // From the first occurrence itself, the next run is tomorrow's.
    expect(job.nextRuns(2, insideOverlap).map(iso)).toEqual([
      "2026-11-02T06:30:00.000Z",
      "2026-11-03T06:30:00.000Z",
    ]);
  });
});

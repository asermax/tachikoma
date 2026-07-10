import { describe, expect, it } from "vitest";

import { formatTimestamp, localIsoDate } from "../../src/util/dates.ts";

describe("localIsoDate", () => {
  it("formats YYYY-MM-DD in the given timezone", () => {
    expect(localIsoDate(new Date("2026-06-12T10:34:56Z"), "UTC")).toBe("2026-06-12");
  });

  it("shifts the calendar day to the configured timezone near midnight", () => {
    // 01:00 UTC is still the previous calendar day in a UTC-3 zone.
    const instant = new Date("2026-06-12T01:00:00Z");

    expect(localIsoDate(instant, "UTC")).toBe("2026-06-12");
    expect(localIsoDate(instant, "America/Argentina/Buenos_Aires")).toBe("2026-06-11");
  });

  it("falls back to the process timezone when none is given", () => {
    expect(localIsoDate(new Date("2026-06-12T10:34:56Z"))).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("formatTimestamp", () => {
  it("formats YYYY-MM-DD HH:mm and the zone abbreviation in UTC", () => {
    expect(formatTimestamp(new Date("2026-06-12T10:34:56Z"), "UTC")).toBe("2026-06-12 10:34 UTC");
  });

  it("renders the local time in a non-UTC timezone", () => {
    // Buenos Aires is UTC-3 (no DST): 10:34 UTC -> 07:34 local. The zone token is ICU-dependent
    // (ART/GMT-3), so assert the local date/time portion and that it moved off UTC.
    const formatted = formatTimestamp(
      new Date("2026-06-12T10:34:56Z"),
      "America/Argentina/Buenos_Aires",
    );

    expect(formatted).toMatch(/^2026-06-12 07:34 /);
    expect(formatted).not.toContain("UTC");
  });

  it("shifts the day near midnight in a non-UTC timezone", () => {
    const formatted = formatTimestamp(
      new Date("2026-06-12T01:00:00Z"),
      "America/Argentina/Buenos_Aires",
    );

    expect(formatted).toMatch(/^2026-06-11 22:00 /);
  });

  it("formats midnight (00:xx) without rolling to 24:xx", () => {
    // Guards against the Node hour12:false "24:00" pitfall — hourCycle h23 yields 00.
    expect(formatTimestamp(new Date("2026-06-12T00:05:00Z"), "UTC")).toBe("2026-06-12 00:05 UTC");
  });
});

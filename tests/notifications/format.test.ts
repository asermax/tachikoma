import { describe, expect, it } from "vitest";

import { formatDigest, formatNotification } from "../../src/extensions/notifications/format.ts";
import type { NotifyPayload } from "../../src/extensions/notifications/payload.ts";

const NOW = new Date("2026-06-12T10:34:56Z");

const payload = (overrides: Partial<NotifyPayload> = {}): NotifyPayload => ({
  text: "Something happened.",
  severity: "info",
  source: "tasks",
  ...overrides,
});

describe("formatNotification", () => {
  it("renders the source/time prefix and includes the title block when present", () => {
    const result = formatNotification(payload({ title: "Heads up" }), NOW, "UTC");

    expect(result).toBe(
      "--- Notification ---\nSource: tasks\nTime: 2026-06-12 10:34 UTC\n\nHeads up\n\nSomething happened.",
    );
  });

  it("omits the title block when there is no title", () => {
    const result = formatNotification(payload(), NOW, "UTC");

    expect(result).toBe(
      "--- Notification ---\nSource: tasks\nTime: 2026-06-12 10:34 UTC\n\nSomething happened.",
    );
  });

  it("renders the time in the configured timezone, not UTC", () => {
    // Buenos Aires is UTC-3 (no DST): 10:34 UTC -> 07:34 local. The zone abbreviation is
    // ICU-dependent (ART/GMT-3), so assert the local date/time portion rather than the token.
    const result = formatNotification(payload(), NOW, "America/Argentina/Buenos_Aires");

    expect(result).toMatch(/Time: 2026-06-12 07:34 /);
    expect(result).not.toContain("10:34");
  });
});

describe("formatDigest", () => {
  it("renders each item with its severity and source, using the title when present", () => {
    const result = formatDigest(
      [
        payload({ title: "First", text: "One.", severity: "warning", source: "git" }),
        payload({ text: "Two.", severity: "urgent", source: "memory" }),
      ],
      NOW,
      "UTC",
    );

    expect(result).toBe(
      [
        "--- Notifications digest ---",
        "Time: 2026-06-12 10:34 UTC",
        "",
        "— Item 1 (warning, source: git) —",
        "First",
        "One.",
        "",
        "— Item 2 (urgent, source: memory) —",
        "Two.",
        "",
      ].join("\n"),
    );
  });

  it("renders an empty digest with only the header", () => {
    expect(formatDigest([], NOW, "UTC")).toBe(
      "--- Notifications digest ---\nTime: 2026-06-12 10:34 UTC\n",
    );
  });
});

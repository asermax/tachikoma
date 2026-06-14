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
    const result = formatNotification(payload({ title: "Heads up" }), NOW);

    expect(result).toBe(
      "--- Notification ---\nSource: tasks\nTime: 2026-06-12 10:34 UTC\n\nHeads up\n\nSomething happened.",
    );
  });

  it("omits the title block when there is no title", () => {
    const result = formatNotification(payload(), NOW);

    expect(result).toBe(
      "--- Notification ---\nSource: tasks\nTime: 2026-06-12 10:34 UTC\n\nSomething happened.",
    );
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
    expect(formatDigest([], NOW)).toBe(
      "--- Notifications digest ---\nTime: 2026-06-12 10:34 UTC\n",
    );
  });
});

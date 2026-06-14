import { describe, expect, it } from "vitest";

import { buildDigest } from "../../src/channels/delivery-digest.ts";
import type { QueuedItem } from "../../src/channels/delivery-queue.ts";

const item = (tier: QueuedItem["tier"], enqueuedAt: number, text: string): QueuedItem => ({
  text,
  tier,
  enqueuedAt,
});

describe("buildDigest", () => {
  it("wraps items in the queued-notifications block with a preamble and one bullet each", () => {
    const digest = buildDigest([item("normal", 1, "task A done"), item("low", 2, "fyi B")]);

    expect(digest.startsWith("<queued-notifications>")).toBe(true);
    expect(digest.endsWith("</queued-notifications>")).toBe(true);
    expect(digest).toContain("- task A done");
    expect(digest).toContain("- fyi B");
  });

  it("orders bullets by tier then FIFO regardless of input order", () => {
    const digest = buildDigest([
      item("low", 1, "low"),
      item("urgent", 3, "urgent"),
      item("normal", 1, "normal-a"),
      item("normal", 2, "normal-b"),
    ]);

    expect(digest.indexOf("urgent")).toBeLessThan(digest.indexOf("normal-a"));
    expect(digest.indexOf("normal-a")).toBeLessThan(digest.indexOf("normal-b"));
    expect(digest.indexOf("normal-b")).toBeLessThan(digest.indexOf("low"));
  });
});

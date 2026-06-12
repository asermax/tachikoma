import { describe, expect, it, vi } from "vitest";

import { type Classifier, detectBoundary } from "../../src/extensions/boundary/detector.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { error: vi.fn(), warn: vi.fn() } as unknown as Logger;

const classifierReturning = (value: unknown): Classifier => ({
  classify: vi.fn().mockResolvedValue(value),
});

const input = {
  message: "actually, about that trip to Japan",
  activeSummary: "Debugging a TypeScript build issue",
  lastExchange: "user: it fails\nassistant: try clearing the cache",
  candidates: [{ id: 7, summary: "Planning a trip to Japan in autumn" }],
};

describe("detectBoundary", () => {
  it("passes through a valid resume decision", async () => {
    const decision = await detectBoundary(
      classifierReturning({ decision: "resume", resumeSessionId: 7 }),
      input,
      fakeLog,
    );

    expect(decision).toEqual({ decision: "resume", resumeSessionId: 7 });
  });

  it("downgrades resume with an unknown session id to continue", async () => {
    const decision = await detectBoundary(
      classifierReturning({ decision: "resume", resumeSessionId: 99 }),
      input,
      fakeLog,
    );

    expect(decision).toEqual({ decision: "continue" });
  });

  it("falls back to continue when classification throws", async () => {
    const classifier: Classifier = {
      classify: vi.fn().mockRejectedValue(new Error("model down")),
    };

    const decision = await detectBoundary(classifier, input, fakeLog);

    expect(decision).toEqual({ decision: "continue" });
  });
});

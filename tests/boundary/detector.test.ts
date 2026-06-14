import { describe, expect, it, vi } from "vitest";

import { type Classifier, detectBoundary } from "../../src/extensions/boundary/detector.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { error: vi.fn(), warn: vi.fn() } as unknown as Logger;

const classifierReturning = (value: unknown): Classifier => ({
  classify: vi.fn().mockResolvedValue(value),
});

const renderedUser = (classifier: Classifier): string =>
  vi.mocked(classifier.classify).mock.calls[0]?.[0].user ?? "";

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

  it("passes through a continue decision without validation", async () => {
    const decision = await detectBoundary(
      classifierReturning({ decision: "continue" }),
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

  it("renders a placeholder summary and omits optional sections when fields are absent", async () => {
    const classifier = classifierReturning({ decision: "continue" });

    await detectBoundary(
      classifier,
      { message: "just a ping", activeSummary: null, lastExchange: null, candidates: [] },
      fakeLog,
    );

    const user = renderedUser(classifier);

    expect(user).toContain("no session is active");
    expect(user).not.toContain("<last-exchange>");
    expect(user).not.toContain("<closed-sessions>");
    expect(user).toContain("<incoming-message>\njust a ping\n</incoming-message>");
  });

  it("renders every optional section when fields are present", async () => {
    const classifier = classifierReturning({ decision: "continue" });

    await detectBoundary(classifier, input, fakeLog);

    const user = renderedUser(classifier);

    expect(user).toContain("<active-session-summary>");
    expect(user).toContain("<last-exchange>");
    expect(user).toContain("<closed-sessions>\n- id 7: Planning a trip to Japan in autumn");
  });
});

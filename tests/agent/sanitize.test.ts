import { describe, expect, it } from "vitest";

import { sanitizeText } from "../../src/agent/sanitize.ts";

describe("sanitizeText", () => {
  it("leaves plain text untouched", () => {
    expect(sanitizeText("hello world")).toBe("hello world");
  });

  it("preserves valid surrogate pairs (astral characters)", () => {
    const emoji = "🤖✨";
    expect(sanitizeText(`pi ${emoji} done`)).toBe(`pi ${emoji} done`);
  });

  it("strips a lone high surrogate", () => {
    expect(sanitizeText("ab\uD800cd")).toBe("abcd");
  });

  it("strips a lone low surrogate", () => {
    expect(sanitizeText("ab\uDC00cd")).toBe("abcd");
  });

  it("strips lone surrogates while keeping adjacent valid pairs", () => {
    expect(sanitizeText("\uD800🤖\uDFFF")).toBe("🤖");
  });

  it("produces output that re-encodes as UTF-8 without throwing", () => {
    const sanitized = sanitizeText("payload \uD834 tail");
    expect(() => Buffer.from(sanitized, "utf-8")).not.toThrow();
    expect(sanitized).toBe("payload  tail");
  });
});

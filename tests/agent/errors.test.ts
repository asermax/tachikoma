import { describe, expect, it } from "vitest";

import { classifyError, classifyErrorKind } from "../../src/agent/errors.ts";

describe("classifyErrorKind", () => {
  it("flags authentication failures", () => {
    expect(classifyErrorKind("Authentication failed (401)")).toBe("auth");
    expect(classifyErrorKind("invalid api key")).toBe("auth");
  });

  it("flags billing and quota failures", () => {
    expect(classifyErrorKind("insufficient_quota for this account")).toBe("billing");
    expect(classifyErrorKind("Monthly usage limit reached")).toBe("billing");
  });

  it("flags encoding failures", () => {
    expect(classifyErrorKind("UnicodeEncodeError: surrogates not allowed")).toBe("encoding");
    expect(classifyErrorKind("cannot encode invalid utf-8 byte sequence")).toBe("encoding");
  });

  it("flags transient provider failures", () => {
    expect(classifyErrorKind("overloaded_error from provider")).toBe("provider");
    expect(classifyErrorKind("HTTP 503 service unavailable")).toBe("provider");
    expect(classifyErrorKind("fetch failed")).toBe("provider");
  });

  it("falls back to unknown", () => {
    expect(classifyErrorKind("something nobody anticipated")).toBe("unknown");
  });
});

describe("classifyError", () => {
  it("treats auth and billing as non-recoverable", () => {
    expect(classifyError("authentication failed")).toEqual({
      errorKind: "auth",
      recoverable: false,
    });
    expect(classifyError("quota exceeded")).toEqual({ errorKind: "billing", recoverable: false });
  });

  it("treats encoding, provider, and unknown as recoverable", () => {
    expect(classifyError("surrogates not allowed")).toEqual({
      errorKind: "encoding",
      recoverable: true,
    });
    expect(classifyError("rate limit exceeded")).toEqual({
      errorKind: "provider",
      recoverable: true,
    });
    expect(classifyError("mystery failure")).toEqual({ errorKind: "unknown", recoverable: true });
  });
});

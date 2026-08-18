import { describe, expect, it } from "vitest";

describe("toolchain", () => {
  it("resolves the pi SDK packages", async () => {
    const ai = await import("@earendil-works/pi-ai");

    expect(typeof ai.contentText).toBe("function");
  });

  it("resolves typebox", async () => {
    const { Type } = await import("typebox");

    expect(Type.Object({ name: Type.String() })).toBeDefined();
  });
});

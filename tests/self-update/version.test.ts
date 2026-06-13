import { describe, expect, it } from "vitest";

import {
  compareVersions,
  isNewerVersion,
  parseVersion,
} from "../../src/extensions/self-update/version.ts";

describe("parseVersion", () => {
  it("parses a plain semver", () => {
    expect(parseVersion("2.0.1")).toEqual({ major: 2, minor: 0, patch: 1, prerelease: null });
  });

  it("strips a leading v and captures the prerelease", () => {
    expect(parseVersion("v3.4.5-rc.1")).toEqual({
      major: 3,
      minor: 4,
      patch: 5,
      prerelease: "rc.1",
    });
  });

  it("returns null for garbage", () => {
    expect(parseVersion("not-a-version")).toBeNull();
    expect(parseVersion("")).toBeNull();
  });
});

describe("compareVersions", () => {
  const v = (raw: string) => parseVersion(raw) as NonNullable<ReturnType<typeof parseVersion>>;

  it("orders by major, then minor, then patch", () => {
    expect(compareVersions(v("2.0.0"), v("1.9.9"))).toBeGreaterThan(0);
    expect(compareVersions(v("1.2.0"), v("1.3.0"))).toBeLessThan(0);
    expect(compareVersions(v("1.2.3"), v("1.2.3"))).toBe(0);
  });

  it("ranks a prerelease below its release", () => {
    expect(compareVersions(v("1.0.0-rc.1"), v("1.0.0"))).toBeLessThan(0);
    expect(compareVersions(v("1.0.0"), v("1.0.0-rc.1"))).toBeGreaterThan(0);
  });
});

describe("isNewerVersion", () => {
  it("is true only for a strictly greater stable release", () => {
    expect(isNewerVersion("2.0.1", "2.0.2")).toBe(true);
    expect(isNewerVersion("2.0.1", "2.1.0")).toBe(true);
    expect(isNewerVersion("2.0.1", "3.0.0")).toBe(true);
  });

  it("is false when equal or older", () => {
    expect(isNewerVersion("2.0.1", "2.0.1")).toBe(false);
    expect(isNewerVersion("2.0.1", "2.0.0")).toBe(false);
    expect(isNewerVersion("2.0.1", "1.9.9")).toBe(false);
  });

  it("never treats a prerelease latest as an upgrade", () => {
    expect(isNewerVersion("2.0.1", "2.1.0-rc.1")).toBe(false);
    expect(isNewerVersion("2.0.1", "3.0.0-beta")).toBe(false);
  });

  it("is false when either side is unparseable", () => {
    expect(isNewerVersion("2.0.1", "latest")).toBe(false);
    expect(isNewerVersion("garbage", "2.0.2")).toBe(false);
  });
});

import { describe, expect, it, vi } from "vitest";

import {
  type ShowProperty,
  SystemctlScopeInspector,
  scopeUnitName,
} from "../../src/extensions/detached-processes/cgroup.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = () => ({ debug: vi.fn() }) as unknown as Logger;

const showing =
  (values: Record<string, string>): ShowProperty =>
  async (_unit, property) =>
    values[property] ?? "";

describe("scopeUnitName", () => {
  it("derives a deterministic scope unit from the record id", () => {
    expect(scopeUnitName("abc-123")).toBe("tachikoma-abc-123.scope");
  });
});

describe("SystemctlScopeInspector.readMemoryCurrentMb", () => {
  it("converts MemoryCurrent bytes to rounded MB", async () => {
    const inspector = new SystemctlScopeInspector(
      fakeLog(),
      showing({ MemoryCurrent: "24961024" }),
    );

    expect(await inspector.readMemoryCurrentMb("tachikoma-x.scope")).toBe(24);
  });

  it("returns null when the property is unset on a missing scope", async () => {
    const inspector = new SystemctlScopeInspector(
      fakeLog(),
      showing({ MemoryCurrent: "[not set]" }),
    );

    expect(await inspector.readMemoryCurrentMb("tachikoma-x.scope")).toBeNull();
  });

  it("returns null for the u64 'infinity' sentinel", async () => {
    const inspector = new SystemctlScopeInspector(
      fakeLog(),
      showing({ MemoryCurrent: "18446744073709551615" }),
    );

    expect(await inspector.readMemoryCurrentMb("tachikoma-x.scope")).toBeNull();
  });

  it("returns null when systemctl is unavailable", async () => {
    const inspector = new SystemctlScopeInspector(fakeLog(), async () => {
      throw new Error("systemctl: not found");
    });

    expect(await inspector.readMemoryCurrentMb("tachikoma-x.scope")).toBeNull();
  });
});

describe("SystemctlScopeInspector.wasOomKilled", () => {
  it("is true when the scope Result is oom-kill", async () => {
    const inspector = new SystemctlScopeInspector(fakeLog(), showing({ Result: "oom-kill" }));

    expect(await inspector.wasOomKilled("tachikoma-x.scope")).toBe(true);
  });

  it("is false for a successful (or absent) scope", async () => {
    const inspector = new SystemctlScopeInspector(fakeLog(), showing({ Result: "success" }));

    expect(await inspector.wasOomKilled("tachikoma-x.scope")).toBe(false);
  });

  it("is false when systemctl is unavailable", async () => {
    const inspector = new SystemctlScopeInspector(fakeLog(), async () => {
      throw new Error("systemctl: not found");
    });

    expect(await inspector.wasOomKilled("tachikoma-x.scope")).toBe(false);
  });
});

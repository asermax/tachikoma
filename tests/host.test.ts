import { describe, expect, it } from "vitest";

import type { SessionScope } from "../src/extensions/api.ts";
import { factoryBindingTargets } from "../src/extensions/host.ts";

describe("factoryBindingTargets", () => {
  it("binds the main session only when scopes are omitted (AC1)", () => {
    expect(factoryBindingTargets()).toEqual({ main: true, background: false });
    expect(factoryBindingTargets({})).toEqual({ main: true, background: false });
  });

  it('treats explicit ["main"] like the omitted default (AC5)', () => {
    expect(factoryBindingTargets({ sessionScopes: ["main"] })).toEqual({
      main: true,
      background: false,
    });
  });

  it("binds both lists when both scopes are present (AC2)", () => {
    expect(factoryBindingTargets({ sessionScopes: ["main", "background"] })).toEqual({
      main: true,
      background: true,
    });
  });

  it("binds background only when main is absent (AC3)", () => {
    expect(factoryBindingTargets({ sessionScopes: ["background"] })).toEqual({
      main: false,
      background: true,
    });
  });

  it("binds neither list for an empty scope array, without throwing (AC4)", () => {
    expect(factoryBindingTargets({ sessionScopes: [] })).toEqual({
      main: false,
      background: false,
    });
  });

  it("ignores out-of-union scopes rather than throwing (AC6)", () => {
    expect(factoryBindingTargets({ sessionScopes: ["main", "unknown"] as SessionScope[] })).toEqual(
      { main: true, background: false },
    );
  });
});

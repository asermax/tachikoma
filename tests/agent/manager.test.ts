import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { isolatedLoaderOptions, selectExtensionFactories } from "../../src/agent/manager.ts";

describe("isolatedLoaderOptions", () => {
  it("suppresses pi's append, project context files, and skills catalog (AC4)", () => {
    const options = isolatedLoaderOptions();

    expect(options.appendSystemPromptOverride()).toEqual([]);
    expect(options.noContextFiles).toBe(true);
    expect(options.noSkills).toBe(true);
  });
});

describe("selectExtensionFactories", () => {
  const all = [{ id: "a" }, { id: "b" }, { id: "c" }] as unknown as ExtensionFactory[];
  const background = [all[0], all[2]] as ExtensionFactory[];
  const sources = { piFactories: all, backgroundFactories: background };

  it("binds the background subset for background task runs", () => {
    expect(
      selectExtensionFactories({ bindBackgroundFactories: true, bare: true }, sources),
    ).toEqual(background);
  });

  it("binds nothing for other bare side runs", () => {
    expect(selectExtensionFactories({ bare: true }, sources)).toEqual([]);
  });

  it("binds every factory for a normal (non-bare) session", () => {
    expect(selectExtensionFactories({ bare: false }, sources)).toEqual(all);
  });
});

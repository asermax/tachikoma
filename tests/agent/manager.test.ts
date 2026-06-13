import { describe, expect, it } from "vitest";

import { isolatedLoaderOptions } from "../../src/agent/manager.ts";

describe("isolatedLoaderOptions", () => {
  it("suppresses pi's append, project context files, and skills catalog (AC4)", () => {
    const options = isolatedLoaderOptions();

    expect(options.appendSystemPromptOverride()).toEqual([]);
    expect(options.noContextFiles).toBe(true);
    expect(options.noSkills).toBe(true);
  });
});

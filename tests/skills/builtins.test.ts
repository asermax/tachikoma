import { describe, expect, it } from "vitest";

import { BUILTIN_AGENTS } from "../../src/extensions/skills/builtins.ts";

describe("BUILTIN_AGENTS", () => {
  it("ships a read-only, default-tier general-purpose agent with a bare name", () => {
    const general = BUILTIN_AGENTS.find((agent) => agent.name === "general-purpose");

    expect(general).toBeDefined();
    expect(general?.name).not.toContain("/"); // bare name cannot collide with <skill>/<agent>
    expect(general?.tools).toBeNull(); // falls back to the delegate default read-only set
    expect(general?.model).toBeNull(); // runs on the side-runner default tier
    expect(general?.systemPrompt.length).toBeGreaterThan(0);
    expect(general?.skill).toBe("built-in");
  });
});

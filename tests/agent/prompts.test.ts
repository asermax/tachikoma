import { describe, expect, it } from "vitest";

import {
  buildBackgroundSystemPrompt,
  buildMainSystemPrompt,
  OPERATIONAL_GUIDANCE,
  SUBAGENT_SYSTEM_PROMPT,
} from "../../src/agent/prompts.ts";

// pi's native coding-agent base opens with this phrase; no role prompt should inherit it.
const PI_NATIVE_BASE = "expert coding assistant operating inside pi";

describe("role system prompts", () => {
  it("share the single OPERATIONAL_GUIDANCE block across every context (AC1)", () => {
    const main = buildMainSystemPrompt({ workspaceRoot: "/ws" });
    const background = buildBackgroundSystemPrompt({ dateHeader: "Monday UTC" });

    for (const prompt of [main, background, SUBAGENT_SYSTEM_PROMPT]) {
      expect(prompt).toContain(OPERATIONAL_GUIDANCE);
    }
  });

  it("are self-contained — no pi-native base, no machine-config inheritance (AC7)", () => {
    const main = buildMainSystemPrompt({ workspaceRoot: "/ws" });
    const background = buildBackgroundSystemPrompt({ dateHeader: "Monday UTC" });

    for (const prompt of [main, background, SUBAGENT_SYSTEM_PROMPT]) {
      expect(prompt).not.toContain(PI_NATIVE_BASE);
      expect(prompt).not.toContain("APPEND_SYSTEM");
    }
  });

  it("builds the main base prompt from identity, hygiene, delegate-awareness, and workspace root — not SOUL/USER (AC5)", () => {
    const prompt = buildMainSystemPrompt({ workspaceRoot: "/home/me/workspace" });

    expect(prompt).toContain("personal assistant");
    expect(prompt).toContain("Workspace root: /home/me/workspace");
    expect(prompt).toContain("delegate_to_agent");
    expect(prompt).toContain("hard to reverse");
    // SOUL/USER are appended via provideContext, not part of the core base prompt.
    expect(prompt).not.toContain("# Soul");
    expect(prompt).not.toContain("# User");
  });

  it("composes the passed dateHeader into the background prompt with autonomy bits (AC1)", () => {
    const prompt = buildBackgroundSystemPrompt({
      dateHeader: "Monday, June 13, 2026, 14:30:00 UTC",
    });

    expect(prompt).toContain("Current date and time: Monday, June 13, 2026, 14:30:00 UTC");
    expect(prompt).toContain("notify_user");
    expect(prompt).toContain("scheduled task");
  });

  it("tells main and background to proactively evaluate skills, but not the skill-less subagent", () => {
    const main = buildMainSystemPrompt({ workspaceRoot: "/ws" });
    const background = buildBackgroundSystemPrompt({ dateHeader: "Monday UTC" });

    expect(main).toContain("evaluate the available skills");
    expect(background).toContain("evaluate the available skills");
    expect(SUBAGENT_SYSTEM_PROMPT).not.toContain("evaluate the available skills");
  });

  it("frames the subagent as a read-only worker whose final message is the result (AC1)", () => {
    expect(SUBAGENT_SYSTEM_PROMPT).toContain("final message IS the result");
    expect(SUBAGENT_SYSTEM_PROMPT).toContain("read-only");
    // pi appends date/cwd even under a custom prompt, so the subagent prompt must not duplicate them.
    expect(SUBAGENT_SYSTEM_PROMPT).not.toContain("Current date");
    expect(SUBAGENT_SYSTEM_PROMPT).not.toContain("working directory");
  });
});

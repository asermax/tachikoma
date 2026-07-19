import { describe, expect, it } from "vitest";

import {
  buildBackgroundSystemPrompt,
  buildMainSystemPrompt,
  buildSubagentSystemPrompt,
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

  it("composes a tz-aware date header into the main prompt when provided", () => {
    const prompt = buildMainSystemPrompt({
      workspaceRoot: "/ws",
      dateHeader: "2026-07-10 (America/Argentina/Buenos_Aires)",
    });

    expect(prompt).toContain("Current date: 2026-07-10 (America/Argentina/Buenos_Aires)");
  });

  it("omits the date header when none is provided", () => {
    expect(buildMainSystemPrompt({ workspaceRoot: "/ws" })).not.toContain("Current date:");
  });

  it("composes the passed dateHeader into the background prompt with autonomy bits (AC1)", () => {
    const prompt = buildBackgroundSystemPrompt({
      dateHeader: "Monday, June 13, 2026, 14:30:00 UTC",
    });

    expect(prompt).toContain("Current date and time: Monday, June 13, 2026, 14:30:00 UTC");
    expect(prompt).toContain("notify_user");
    expect(prompt).toContain("scheduled task");
  });

  it("tells the background agent to work a goal and self-declare via update_goal (R13)", () => {
    const prompt = buildBackgroundSystemPrompt({ dateHeader: "Monday UTC" });

    expect(prompt).toContain("goal");
    expect(prompt).toContain("end state");
    expect(prompt).toContain("stated check");
    expect(prompt).toContain("invariants");
    expect(prompt).toContain("update_goal");
  });

  it("keeps the core base prompts skill-agnostic — the skills extension owns that guidance", () => {
    // Skill-following guidance moved to the skills extension (src/extensions/skills/usage.ts) so the
    // core base prompt stays feature-agnostic and the guidance appears only when skills are enabled.
    const main = buildMainSystemPrompt({ workspaceRoot: "/ws" });
    const background = buildBackgroundSystemPrompt({ dateHeader: "Monday UTC" });

    for (const prompt of [main, background, SUBAGENT_SYSTEM_PROMPT]) {
      expect(prompt).not.toMatch(/skill/i);
    }
  });

  it("frames the subagent as a read-only worker whose final message is the result (AC1)", () => {
    expect(SUBAGENT_SYSTEM_PROMPT).toContain("final message IS the result");
    expect(SUBAGENT_SYSTEM_PROMPT).toContain("read-only");
    // pi appends date/cwd even under a custom prompt, so the subagent prompt must not duplicate them.
    expect(SUBAGENT_SYSTEM_PROMPT).not.toContain("Current date");
    expect(SUBAGENT_SYSTEM_PROMPT).not.toContain("working directory");
  });
});

describe("buildSubagentSystemPrompt", () => {
  it("frames a read-only tool set as read-only and lists the granted tools", () => {
    const prompt = buildSubagentSystemPrompt({ tools: ["read", "grep"] });

    expect(prompt).toContain("read-only");
    expect(prompt).toContain("you have the read, grep tools");
    expect(prompt).not.toContain("Modify files or run commands");
    expect(prompt).toContain(OPERATIONAL_GUIDANCE);
  });

  it("switches to a mutation/exec framing when bash, edit, or write is granted", () => {
    for (const tools of [
      ["read", "bash"],
      ["read", "edit"],
      ["read", "write"],
    ] as string[][]) {
      const prompt = buildSubagentSystemPrompt({ tools });

      expect(prompt).not.toContain("read-only");
      expect(prompt).toContain("Modify files or run commands");
      expect(prompt).toContain(`You have these tools: ${tools.join(", ")}.`);
      expect(prompt).toContain(OPERATIONAL_GUIDANCE);
    }
  });

  it("keeps the final-message-is-the-result framing regardless of tools", () => {
    expect(buildSubagentSystemPrompt({ tools: ["read"] })).toContain("final message IS the result");
    expect(buildSubagentSystemPrompt({ tools: ["read", "bash"] })).toContain(
      "final message IS the result",
    );
  });

  it("reproduces the read-only default prompt's tool list for the four read tools", () => {
    expect(buildSubagentSystemPrompt({ tools: ["read", "grep", "find", "ls"] })).toContain(
      "you have the read, grep, find, ls tools",
    );
  });
});

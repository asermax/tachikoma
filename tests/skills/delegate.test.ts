import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import type { SkillAgent } from "../../src/extensions/skills/agents.ts";
import { BUILTIN_AGENTS } from "../../src/extensions/skills/builtins.ts";
import { type AgentRunner, createDelegateTool } from "../../src/extensions/skills/delegate.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn() } as unknown as Logger;
const fakeCtx = {} as ExtensionContext;

const agents: SkillAgent[] = [
  {
    name: "research/scout",
    description: "Finds sources",
    tools: ["read", "grep"],
    model: null,
    systemPrompt: "You are a scout.",
    skill: "research",
  },
  {
    name: "research/writer",
    description: "Drafts summaries",
    tools: null,
    model: null,
    systemPrompt: "You write.",
    skill: "research",
  },
  {
    name: "research/analyst",
    description: "Deep analysis",
    tools: null,
    model: "anthropic/claude-opus-4-5:high",
    systemPrompt: "You analyze.",
    skill: "research",
  },
];

const makeTool = (runner: AgentRunner) =>
  createDelegateTool({ discover: () => agents, runner, log: fakeLog });

describe("delegate_to_agent tool", () => {
  it("lists discovered agents in the tool description", () => {
    const tool = makeTool({ run: vi.fn() });

    expect(tool.description).toContain("research/scout: Finds sources");
    expect(tool.description).toContain("research/writer: Drafts summaries");
  });

  it("runs the agent headlessly with its system prompt and tools", async () => {
    const run = vi.fn().mockResolvedValue({ text: "found three sources" });
    const tool = makeTool({ run });

    const result = await tool.execute(
      "call-1",
      { agent: "research/scout", task: "find sources on topic X" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith({
      system: "You are a scout.",
      tools: ["read", "grep"],
      prompt: "find sources on topic X",
      isolatePrompt: true,
    });
    expect(run.mock.calls[0]?.[0]).not.toHaveProperty("model");
    expect(result.content).toEqual([{ type: "text", text: "found three sources" }]);
  });

  it("runs an agent with its declared model frontmatter on that model", async () => {
    const run = vi.fn().mockResolvedValue({ text: "analysis" });
    const tool = makeTool({ run });

    await tool.execute(
      "call-model",
      { agent: "research/analyst", task: "analyze X" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({ model: "anthropic/claude-opus-4-5:high" }),
    );
  });

  it("falls back to the default read-only tool set", async () => {
    const run = vi.fn().mockResolvedValue({ text: "draft" });
    const tool = makeTool({ run });

    await tool.execute(
      "call-2",
      { agent: "research/writer", task: "summarize" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({ tools: ["read", "grep", "find", "ls"] }),
    );
  });

  it("rejects unknown agents listing the available ones", async () => {
    const run = vi.fn();
    const tool = makeTool({ run });

    await expect(
      tool.execute("call-3", { agent: "nope", task: "anything" }, undefined, undefined, fakeCtx),
    ).rejects.toThrow(/Unknown agent "nope"[\s\S]*research\/scout/);
    expect(run).not.toHaveBeenCalled();
  });

  it("truncates oversized agent output with a marker (AC3b)", async () => {
    const huge = Array(3000).fill("line").join("\n");
    const run = vi.fn().mockResolvedValue({ text: huge });
    const tool = makeTool({ run });

    const result = await tool.execute(
      "call-4",
      { agent: "research/scout", task: "dump everything" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect((result.content[0] as { text: string }).text).toContain("[output truncated]");
  });
});

describe("delegate_to_agent with the built-in general-purpose agent", () => {
  const toolFor = (runner: AgentRunner) =>
    createDelegateTool({ discover: () => BUILTIN_AGENTS, runner, log: fakeLog });

  it("lists general-purpose as an available agent (AC2)", () => {
    const tool = toolFor({ run: vi.fn() });

    expect(tool.description).toContain("general-purpose:");
  });

  it("lists the built-in general-purpose agent before skill agents (AC6)", () => {
    const tool = createDelegateTool({
      discover: () => [...BUILTIN_AGENTS, ...agents],
      runner: { run: vi.fn() },
      log: fakeLog,
    });

    expect(tool.description.indexOf("general-purpose:")).toBeLessThan(
      tool.description.indexOf("research/scout:"),
    );
  });

  it("runs general-purpose isolated, read-only, and on the default tier (AC3a/AC4)", async () => {
    const run = vi.fn().mockResolvedValue({ text: "explored" });
    const tool = toolFor({ run });

    await tool.execute(
      "call-gp",
      { agent: "general-purpose", task: "find the config loader" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({
        tools: ["read", "grep", "find", "ls"],
        prompt: "find the config loader",
        isolatePrompt: true,
      }),
    );
    expect(run.mock.calls[0]?.[0]).not.toHaveProperty("model");
  });
});

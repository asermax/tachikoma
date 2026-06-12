import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import type { SkillAgent } from "../../src/extensions/skills/agents.ts";
import { type AgentRunner, createDelegateTool } from "../../src/extensions/skills/delegate.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn() } as unknown as Logger;
const fakeCtx = {} as ExtensionContext;

const agents: SkillAgent[] = [
  {
    name: "research/scout",
    description: "Finds sources",
    tools: ["read", "grep"],
    systemPrompt: "You are a scout.",
    skill: "research",
  },
  {
    name: "research/writer",
    description: "Drafts summaries",
    tools: null,
    systemPrompt: "You write.",
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
    });
    expect(result.content).toEqual([{ type: "text", text: "found three sources" }]);
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
});

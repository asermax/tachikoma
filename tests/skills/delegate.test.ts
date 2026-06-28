import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import { buildSubagentSystemPrompt } from "../../src/agent/prompts.ts";
import type { SkillAgent } from "../../src/extensions/skills/agents.ts";
import { BUILTIN_AGENTS } from "../../src/extensions/skills/builtins.ts";
import {
  type AgentRunner,
  createDelegateTool,
  resolveTools,
} from "../../src/extensions/skills/delegate.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = Object.assign(
  { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  { child: () => fakeLog },
) as unknown as Logger;
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
      { agent: "research/scout", task: "find sources on topic X", description: "find sources" },
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
      { agent: "research/analyst", task: "analyze X", description: "analyze topic" },
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
      { agent: "research/writer", task: "summarize", description: "summarize" },
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
      tool.execute(
        "call-3",
        { agent: "nope", task: "anything", description: "anything" },
        undefined,
        undefined,
        fakeCtx,
      ),
    ).rejects.toThrow(/Unknown agent "nope"[\s\S]*research\/scout/);
    expect(run).not.toHaveBeenCalled();
  });

  it("truncates oversized agent output with a marker (AC3b)", async () => {
    const huge = Array(3000).fill("line").join("\n");
    const run = vi.fn().mockResolvedValue({ text: huge });
    const tool = makeTool({ run });

    const result = await tool.execute(
      "call-4",
      { agent: "research/scout", task: "dump everything", description: "dump everything" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect((result.content[0] as { text: string }).text).toContain("[output truncated]");
  });

  it("does not forward the description to the delegated run (display-only)", async () => {
    const run = vi.fn().mockResolvedValue({ text: "done" });
    const tool = makeTool({ run });

    await tool.execute(
      "call-desc",
      { agent: "research/scout", task: "find sources on X", description: "scout sources" },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith({
      system: "You are a scout.",
      tools: ["read", "grep"],
      prompt: "find sources on X",
      isolatePrompt: true,
    });
    expect(run.mock.calls[0]?.[0]).not.toHaveProperty("description");
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
      {
        agent: "general-purpose",
        task: "find the config loader",
        description: "find config loader",
      },
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

describe("delegate_to_agent per-delegation tool selection", () => {
  const toolFor = (runner: AgentRunner) =>
    createDelegateTool({ discover: () => [...BUILTIN_AGENTS, ...agents], runner, log: fakeLog });

  it("overrides the agent's tools when `tools` is given (AC2)", async () => {
    const run = vi.fn().mockResolvedValue({ text: "ok" });
    const tool = toolFor({ run });

    await tool.execute(
      "call-ov",
      {
        agent: "general-purpose",
        task: "run a shell check",
        description: "shell check",
        tools: ["read", "grep", "bash"],
      },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({ tools: ["read", "grep", "bash"], isolatePrompt: true }),
    );
  });

  it("rebuilds the built-in prompt from the granted tools (no read-only claim) (AC2)", async () => {
    const run = vi.fn().mockResolvedValue({ text: "ok" });
    const tool = toolFor({ run });

    await tool.execute(
      "call-prompt",
      {
        agent: "general-purpose",
        task: "run a shell check",
        description: "shell check",
        tools: ["read", "grep", "bash"],
      },
      undefined,
      undefined,
      fakeCtx,
    );

    const system = run.mock.calls[0]?.[0]?.system as string;
    expect(system).toBe(buildSubagentSystemPrompt({ tools: ["read", "grep", "bash"] }));
    expect(system).not.toContain("read-only");
    expect(system).toContain("Modify files or run commands");
  });

  it("keeps a skill agent's own prompt when its tools are overridden", async () => {
    const run = vi.fn().mockResolvedValue({ text: "ok" });
    const tool = toolFor({ run });

    await tool.execute(
      "call-skill",
      {
        agent: "research/scout",
        task: "find then patch",
        description: "scout+patch",
        tools: ["read", "bash"],
      },
      undefined,
      undefined,
      fakeCtx,
    );

    // Override wins over the declared [read, grep]; the skill agent's prompt is used as-is.
    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({ tools: ["read", "bash"], system: "You are a scout." }),
    );
  });

  it("rejects unknown `tools` listing the built-ins and pointing to `extensionTools` (AC3)", async () => {
    const run = vi.fn();
    const tool = toolFor({ run });

    const error = await tool
      .execute(
        "call-bad",
        {
          agent: "general-purpose",
          task: "research the web",
          description: "web research",
          tools: ["read", "web_search"],
        },
        undefined,
        undefined,
        fakeCtx,
      )
      .then(
        () => undefined,
        (err: Error) => err,
      );

    expect(error?.message).toMatch(
      /Unknown tools for delegate_to_agent: web_search[\s\S]*bash[\s\S]*extensionTools/,
    );
    expect(error?.message).not.toContain("not available to subagents");
    expect(run).not.toHaveBeenCalled();
  });

  it("threads `extensionTools` into `runner.run` when given", async () => {
    const run = vi.fn().mockResolvedValue({ text: "searched" });
    const tool = toolFor({ run });

    await tool.execute(
      "call-ext",
      {
        agent: "general-purpose",
        task: "research the web",
        description: "web research",
        extensionTools: ["web_search"],
      },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith(expect.objectContaining({ extensionTools: ["web_search"] }));
  });

  it("omits `extensionTools` when empty or omitted (built-ins still apply, never 'no tools')", async () => {
    // omitted
    const runOmitted = vi.fn().mockResolvedValue({ text: "ok" });
    await toolFor({ run: runOmitted }).execute(
      "call-no-ext",
      { agent: "general-purpose", task: "explore", description: "explore" },
      undefined,
      undefined,
      fakeCtx,
    );
    expect(runOmitted.mock.calls[0]?.[0]).not.toHaveProperty("extensionTools");
    expect(runOmitted).toHaveBeenCalledWith(
      expect.objectContaining({ tools: ["read", "grep", "find", "ls"] }),
    );

    // empty array
    const runEmpty = vi.fn().mockResolvedValue({ text: "ok" });
    await toolFor({ run: runEmpty }).execute(
      "call-empty-ext",
      {
        agent: "general-purpose",
        task: "explore",
        description: "explore",
        extensionTools: [],
      },
      undefined,
      undefined,
      fakeCtx,
    );
    expect(runEmpty.mock.calls[0]?.[0]).not.toHaveProperty("extensionTools");
    expect(runEmpty).toHaveBeenCalledWith(
      expect.objectContaining({ tools: ["read", "grep", "find", "ls"] }),
    );
  });

  it("passes `extensionTools` through without built-in validation in execute", async () => {
    const run = vi.fn().mockResolvedValue({ text: "ok" });
    const tool = toolFor({ run });

    // `web_search` is not a built-in, so `tools: ["web_search"]` would throw — but `extensionTools`
    // is validated source-agnostically in SideRunner, not here, so execute does not throw.
    await expect(
      tool.execute(
        "call-pass",
        {
          agent: "general-purpose",
          task: "research",
          description: "research",
          extensionTools: ["web_search"],
        },
        undefined,
        undefined,
        fakeCtx,
      ),
    ).resolves.toEqual(expect.any(Object));
    expect(run).toHaveBeenCalledWith(expect.objectContaining({ extensionTools: ["web_search"] }));
  });

  it("treats an empty `tools` array as 'not specified' (AC4)", async () => {
    const run = vi.fn().mockResolvedValue({ text: "ok" });
    const tool = toolFor({ run });

    await tool.execute(
      "call-empty",
      {
        agent: "research/scout",
        task: "find sources",
        description: "find sources",
        tools: [],
      },
      undefined,
      undefined,
      fakeCtx,
    );

    expect(run).toHaveBeenCalledWith(expect.objectContaining({ tools: ["read", "grep"] }));
  });
});

describe("resolveTools", () => {
  it("returns the requested set when non-empty, overriding declared/default", () => {
    expect(resolveTools(["read", "bash"], ["read", "grep"])).toEqual(["read", "bash"]);
    expect(resolveTools(["read", "bash"], null)).toEqual(["read", "bash"]);
  });

  it("falls back to the agent's declared tools when nothing is requested", () => {
    expect(resolveTools(undefined, ["read", "grep"])).toEqual(["read", "grep"]);
    expect(resolveTools([], ["read", "grep"])).toEqual(["read", "grep"]);
  });

  it("falls back to the read-only default when neither is set", () => {
    expect(resolveTools(undefined, null)).toEqual(["read", "grep", "find", "ls"]);
    expect(resolveTools([], null)).toEqual(["read", "grep", "find", "ls"]);
  });

  it("throws on unknown tools, naming them, the built-ins, and `extensionTools`", () => {
    expect(() => resolveTools(["read", "web_search"], null)).toThrow(
      /Unknown tools for delegate_to_agent: web_search[\s\S]*bash[\s\S]*extensionTools/,
    );
    expect(() => resolveTools(["read", "web_search"], null)).not.toThrow(
      /not available to subagents/,
    );
  });
});

describe("delegate_to_agent agent-facing guidance", () => {
  const tool = createDelegateTool({
    discover: () => BUILTIN_AGENTS,
    runner: { run: vi.fn() },
    log: fakeLog,
  });

  it("advertises the `extensionTools` grant and drops the 'not available to subagents' messaging", () => {
    const guidelines = (tool.promptGuidelines ?? []).join("\n");

    expect(tool.description).toContain("extensionTools");
    expect(tool.description).not.toContain("not available to subagents");
    expect(guidelines).toContain("extensionTools");
    expect(guidelines).not.toContain("not available to subagents");
  });
});

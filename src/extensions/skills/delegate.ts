import { type ToolDefinition, truncateTail } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { SkillAgent } from "./agents.ts";

export type AgentRunner = Pick<SideRunner, "run">;

export interface DelegateToolOptions {
  /** Called once for the tool description and again on every execution (skills can change). */
  discover: () => SkillAgent[];
  runner: AgentRunner;
  log: Logger;
}

const DEFAULT_AGENT_TOOLS = ["read", "grep", "find", "ls"];

/**
 * pi's built-in tool names — the only tools a delegated subagent can be granted. Extension and web
 * tools are factory tools, unreachable from the bare isolated session a delegation runs in (see
 * DLT-184 for exposing them); the SDK `tools` allowlist admits built-ins alone.
 */
const BUILTIN_TOOL_NAMES = new Set(["read", "grep", "find", "ls", "bash", "edit", "write"]);

/**
 * Resolve a delegated run's tool set. A non-empty per-delegation `requested` list fully overrides
 * the agent's defaults and is validated against the built-ins — an unknown name throws a
 * self-correcting error (only built-ins are grantable), mirroring the unknown-agent error.
 * Otherwise the agent's declared tools are used, falling back to the read-only default. An empty or
 * absent request is treated as "not specified" so it never means "no tools".
 */
export const resolveTools = (
  requested: string[] | undefined,
  declared: string[] | null,
): string[] => {
  if (requested != null && requested.length > 0) {
    const unknown = requested.filter((tool) => !BUILTIN_TOOL_NAMES.has(tool));

    if (unknown.length > 0) {
      throw new Error(
        `Unknown tools for delegate_to_agent: ${unknown.join(", ")}. Only built-in tools are grantable to subagents (${[...BUILTIN_TOOL_NAMES].join(", ")}); web/extension tools are not available to subagents.`,
      );
    }

    return requested;
  }

  return declared ?? DEFAULT_AGENT_TOOLS;
};

const DelegateParams = Type.Object({
  agent: Type.String({
    description: "Agent to delegate to, exactly as listed in the tool description",
  }),
  task: Type.String({
    description:
      "Complete, self-contained task description — the agent has no access to this conversation",
  }),
  description: Type.String({
    description:
      "Short label for this delegation, shown in tool-activity displays; not sent to the agent",
  }),
  tools: Type.Optional(
    Type.Array(
      Type.String({
        description:
          "Optional complete override of the agent's default tools for this run. Only built-in tools are grantable: read, grep, find, ls, bash, edit, write. Omit to use the agent's declared tools (or the read-only default).",
      }),
    ),
  ),
});

const renderAgentList = (agents: SkillAgent[]): string =>
  agents.length > 0
    ? agents.map((agent) => `- ${agent.name}: ${agent.description}`).join("\n")
    : "(none discovered)";

/**
 * `delegate_to_agent`: run an agent definition headlessly — the built-in general-purpose agent or
 * one bundled in a skill — with its own system prompt and tool set, returning its final answer.
 */
export const createDelegateTool = ({
  discover,
  runner,
  log,
}: DelegateToolOptions): ToolDefinition<typeof DelegateParams> => ({
  name: "delegate_to_agent",
  label: "Delegate to agent",

  description: [
    "Delegate a focused task to a specialized agent — the built-in general-purpose agent (for",
    "exploring or searching files and gathering information) or one bundled in a skill.",
    "The agent runs in an isolated context with its own system prompt and tools,",
    "and returns its final answer as the tool result.",
    "",
    "Available agents:",
    renderAgentList(discover()),
    "",
    "By default an agent runs read-only (read, grep, find, ls). Pass `tools` to grant a different",
    "set for this run — only built-in tools are grantable (read, grep, find, ls, bash, edit, write);",
    "web/extension tools are not available to subagents.",
  ].join("\n"),

  promptSnippet:
    "delegate_to_agent: hand a focused task to a general-purpose or skill-bundled agent",
  promptGuidelines: [
    "Use delegate_to_agent to offload focused, context-heavy work (e.g. exploring files) to the general-purpose agent, or when a skill ships an agent suited to the task; pass a complete, self-contained task description.",
    "Pass a short `description` labeling the delegation (a few words, e.g. find all tool-labels references) so tool activity shows what it is for; the description is display-only and is not forwarded to the agent.",
    'An agent is read-only by default; pass `tools` (a complete list of the built-in tools it should have, e.g. ["read", "grep", "bash"]) only when the task needs more than exploration — to run shell commands (bash) or modify files (edit, write).',
  ],

  parameters: DelegateParams,

  async execute(toolCallId, params) {
    const agents = discover();
    const agent = agents.find((candidate) => candidate.name === params.agent);

    if (agent == null) {
      log.warn({ toolCallId, agent: params.agent }, "delegate_to_agent called with unknown agent");

      throw new Error(
        `Unknown agent "${params.agent}". Available agents:\n${renderAgentList(agents)}`,
      );
    }

    log.debug({ toolCallId, agent: agent.name }, "delegating task to skill agent");

    const tools = resolveTools(params.tools, agent.tools);
    // The built-in agents rebuild their prompt from the granted tools (so a worker handed bash is
    // not told it is read-only); skill agents keep their author-authored system prompt.
    const system = agent.dynamicPrompt != null ? agent.dynamicPrompt(tools) : agent.systemPrompt;

    const start = Date.now();

    let result: Awaited<ReturnType<AgentRunner["run"]>>;
    try {
      result = await runner.run({
        system,
        tools,
        prompt: params.task,
        isolatePrompt: true,
        ...(agent.model != null ? { model: agent.model } : {}),
      });
    } catch (error) {
      log.warn(
        { err: error, toolCallId, agent: agent.name, durationMs: Date.now() - start },
        "skill agent delegation failed",
      );

      throw error;
    }

    const { content, truncated } = truncateTail(result.text);

    log.debug(
      {
        toolCallId,
        agent: agent.name,
        chars: result.text.length,
        truncated,
        durationMs: Date.now() - start,
      },
      "skill agent delegation completed",
    );

    return {
      content: [{ type: "text", text: truncated ? `${content}\n\n[output truncated]` : content }],
      details: undefined,
    };
  },
});

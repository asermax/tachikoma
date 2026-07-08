import { type ToolDefinition, truncateTail } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { SkillAgent } from "./agents.ts";
import { BUILTIN_TOOL_NAMES } from "./tool-names.ts";

export type AgentRunner = Pick<SideRunner, "run">;

export interface DelegateToolOptions {
  /** Called once for the tool description and again on every execution (skills can change). */
  discover: () => SkillAgent[];
  runner: AgentRunner;
  log: Logger;
}

const DEFAULT_AGENT_TOOLS = ["read", "grep", "find", "ls"];

/**
 * Resolve a delegated run's built-in tool set. A non-empty per-delegation `requested` list fully
 * overrides the agent's defaults and is validated against the built-ins — the `tools` param is
 * built-in-only, so an unknown name throws a self-correcting error naming the valid built-ins and
 * pointing extension/web tools at `extensionTools`, mirroring the unknown-agent error. Otherwise the
 * agent's declared tools are used, falling back to the read-only default. An empty or absent request
 * is treated as "not specified" so it never means "no tools".
 */
export const resolveTools = (
  requested: string[] | undefined,
  declared: string[] | null,
): string[] => {
  if (requested != null && requested.length > 0) {
    const unknown = requested.filter((tool) => !BUILTIN_TOOL_NAMES.has(tool));

    if (unknown.length > 0) {
      throw new Error(
        `Unknown tools for delegate_to_agent: ${unknown.join(", ")}. Only built-in tools go in \`tools\` (${[...BUILTIN_TOOL_NAMES].join(", ")}); request extension/web tools via \`extensionTools\` instead.`,
      );
    }

    return requested;
  }

  return declared ?? DEFAULT_AGENT_TOOLS;
};

/**
 * Merge an agent's declared `extensionTools` with a per-delegation grant. Both are additive (granted
 * on top of the resolved built-ins), so the effective set is their union, de-duplicated (declared
 * names first, then caller additions — the order is cosmetic; only the set matters). A caller can
 * extend an agent's declared grant but never narrow it: replace-semantics would let a caller silently
 * strip a dependency the author declared, defeating the frontmatter field. Extension tool names are
 * resolved source-agnostically against the opened subagent session in SideRunner.run (ADR-015), so
 * neither set is name-validated here; an empty result means none are granted (the built-in-allowlist
 * path).
 */
export const resolveExtensionTools = (
  declared: string[] | null,
  requested: string[] | undefined,
): string[] => [...new Set([...(declared ?? []), ...(requested ?? [])])];

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
          "Optional complete override of the agent's default tools for this run. Only built-in tools go in `tools`: read, grep, find, ls, bash, edit, write. Omit to use the agent's declared tools (or the read-only default). For extension/web tools, use `extensionTools`.",
      }),
    ),
  ),
  extensionTools: Type.Optional(
    Type.Array(
      Type.String({
        description:
          "Exposed extension tool names to grant this run (e.g. web search/scrape), additive on top of `tools` and the agent's built-in set. Empty/omitted = none granted (never 'no tools'). An unknown name throws before the run, listing the grantable tools.",
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
    "set for this run — only built-in tools go in `tools` (read, grep, find, ls, bash, edit, write).",
    "Extension/web tools can be granted to a subagent via the `extensionTools` parameter (e.g. web",
    "search/scrape for research), added on top of its built-in tools.",
  ].join("\n"),

  promptSnippet:
    "delegate_to_agent: hand a focused task to a general-purpose or skill-bundled agent (extension tools can be granted via `extensionTools`)",
  promptGuidelines: [
    "Use delegate_to_agent to offload focused, context-heavy work (e.g. exploring files) to the general-purpose agent, or when a skill ships an agent suited to the task; pass a complete, self-contained task description.",
    "Pass a short `description` labeling the delegation (a few words, e.g. find all tool-labels references) so tool activity shows what it is for; the description is display-only and is not forwarded to the agent.",
    'An agent is read-only by default; pass `tools` (a complete list of the built-in tools it should have, e.g. ["read", "grep", "bash"]) only when the task needs more than exploration — to run shell commands (bash) or modify files (edit, write).',
    "Delegate tool-dependent work (e.g. web research) to a subagent with the relevant exposed extension tools granted via `extensionTools` (e.g. web search/scrape) rather than performing that work on the main session.",
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
    const extensionTools = resolveExtensionTools(agent.extensionTools, params.extensionTools);
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
        // Additive grant on top of the resolved built-ins: the agent's declared extensionTools
        // merged with the caller's (resolveExtensionTools). Validated source-agnostically in
        // SideRunner (not against BUILTIN_TOOL_NAMES here). Spread only when non-empty so an
        // empty/omitted request takes the built-in-allowlist path unchanged.
        ...(extensionTools.length > 0 ? { extensionTools } : {}),
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

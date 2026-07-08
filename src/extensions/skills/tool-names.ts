/**
 * pi's built-in tool names — the only names valid in skill-agent frontmatter `tools` (and the
 * `delegate_to_agent` `tools` param). The single source within the skills extension: imported by
 * `agents.ts` (frontmatter `tools` name validation) and `delegate.ts` (the unknown-tool error).
 *
 * A second, intentionally-separate copy lives in `src/agent/side-run.ts` because the agent layer
 * must not import from the skills extension (see ADR-015); reconcile into a true shared source only
 * if pi's built-in surface changes.
 */
export const BUILTIN_TOOL_NAMES = new Set(["read", "grep", "find", "ls", "bash", "edit", "write"]);

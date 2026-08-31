import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for skills, injected into the agent's context.
 *
 * Scoped to main and background — both receive the skill sources and (when proactiveLoading
 * is on) proactive injection. This is the skills extension's own usage section (mirroring
 * git-usage / workflows-usage / tasks-usage): it owns the agent-facing skill guidance so the
 * core base prompt stays feature-agnostic, and it only reaches agents whose scope includes it
 * (so a disabled skills extension contributes no guidance). The delegation guidance lives
 * here too — `delegate_to_agent` is this extension's tool.
 */
export const SKILLS_USAGE = `## Skills

Skills package specialized expertise. Some are proactively loaded for the current task — their full instructions are injected directly, so no /skill load is needed. Treat an injected skill as authoritative for its domain: follow its instructions and workflows rather than improvising.

Beyond injected skills, descriptions are available in the catalog; when one fits the task, read its SKILL.md and follow it before proceeding on your own.

For focused, context-heavy sub-tasks — exploring, searching, gathering scattered details — delegate to a subagent with \`delegate_to_agent\`, granting any needed tools (e.g. web search) via its \`extensionTools\` parameter.

${referencePointer(import.meta.dirname, "skills")}`;

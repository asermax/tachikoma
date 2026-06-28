/**
 * Usage guidance for skills, injected into the agent's context.
 *
 * Scoped to main and background — both receive the skill sources and (when proactiveLoading is on)
 * proactive injection. This is the skills extension's own usage section (mirroring git-usage /
 * workflows-usage / tasks-usage): it owns the agent-facing skill guidance so the core base prompt
 * stays feature-agnostic, and it only reaches agents whose scope includes it (so a disabled skills
 * extension contributes no guidance).
 */
export const SKILLS_USAGE = `## Skills

Skills package specialized expertise. Some are proactively loaded for the current task — their full instructions are injected directly, so no /skill load is needed. These injected skills define the correct process for what you are doing: follow their instructions and workflows rather than improvising an alternative approach, and if a skill defines a workflow, use it. Treat an injected skill as authoritative for its domain.

Beyond injected skills, descriptions are available in the catalog; when one fits the task, read its SKILL.md and follow it before proceeding on your own.

When you delegate tool-dependent work (e.g. web research) via \`delegate_to_agent\`, grant the relevant exposed extension tools through its \`extensionTools\` parameter (e.g. web search/scrape) so the work runs in the isolated subagent instead of this session.`;

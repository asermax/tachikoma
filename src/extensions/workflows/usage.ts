import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for the workflow engine, injected into the agent's context.
 * Scoped to main only — workflow tools are bound to the main session.
 */
export const WORKFLOWS_USAGE = `## Workflows

Skills can define ordered, multi-step processes — workflows — that track state across context boundaries, so a long procedure survives compaction and resumes cleanly. Workflows are not auto-detected: read a skill's SKILL.md to see which it offers and when to use them.

Drive an instance with \`update_workflow_state\`, always passing the **top-level** workflow id. A result that starts a step carries its instructions — they are only visible after the step starts. Completing or skipping a step auto-starts the next; the last one auto-finalizes the workflow. Step mechanics: \`(loop: ...)\` halts until you start it with \`items=[...]\` (one run per item; \`[]\` = zero iterations); \`(if: ...)\` halts for an explicit start-or-skip decision on its predicate; \`(composes: ...)\` runs a sub-workflow whose steps you drive with the same id. After context loss, recover your place with \`query_workflow()\`.

${referencePointer(import.meta.dirname, "workflows")}`;

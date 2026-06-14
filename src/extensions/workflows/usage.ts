/**
 * Usage guidance for the workflow engine, injected into the agent's context.
 * Scoped to main only — workflow tools are bound to the main session.
 */
export const WORKFLOWS_USAGE = `## Workflows

Skills can define ordered, multi-step processes — workflows — that track state across context boundaries so a long procedure survives compaction and resumes cleanly. Use one when a task is a defined sequence of steps that must run in order without losing progress (e.g. a planning or review procedure a skill documents).

Workflows are not auto-detected: read a skill's SKILL.md to see which workflows it offers and when to use them.

Tools:
- \`start_workflow\` (\`skill_name\`, \`workflow_name\`) — begin a workflow; returns the step list, a scratchpad path, and the first step's instructions.
- \`update_workflow_state\` (\`workflow_id\`, \`step\`, \`action\`: start | complete | skip; \`items\` for loop steps) — advance a step. Completing or skipping auto-starts the next step (and auto-finalizes the workflow after the last). Always pass the **top-level** workflow id; the engine routes to the deepest active (composed/loop) layer and shows a breadcrumb.
- \`query_workflow\` (optional \`workflow_id\`) — list active workflows, or inspect one's full state. Use it to recover after losing track of where you were.
- \`end_workflow\` (\`workflow_id\`, \`action\`: complete | abort) — only needed to cancel early; normal completion is automatic.`;

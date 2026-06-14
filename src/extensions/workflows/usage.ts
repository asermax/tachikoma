/**
 * Usage guidance for the workflow engine, injected into the agent's context.
 * Scoped to main only — workflow tools are bound to the main session.
 */
export const WORKFLOWS_USAGE = `## Workflows

Skills can define ordered, multi-step processes — workflows — that track state across context boundaries so a long procedure survives compaction and resumes cleanly. Use one when a task is a defined sequence of steps that must run in order without losing progress (e.g. a planning or review procedure a skill documents).

Workflows are not auto-detected: read a skill's SKILL.md to see which workflows it offers and when to use them.

When advancing a step, always pass the **top-level** workflow id; the engine routes to the deepest active (composed/loop) layer and shows a breadcrumb.`;

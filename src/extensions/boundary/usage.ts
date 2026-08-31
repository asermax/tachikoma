import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for the branch model, injected into the main session's context. The boundary
 * extension owns it (DES-005: feature guidance stays with the feature) — the core base prompt
 * documents only the conversation substrate. Scoped to main: only the main conversation is a
 * trunk with branches; background task sessions are single-purpose runs.
 */
export const BOUNDARY_USAGE = `## Branches

The main conversation is a daily trunk of topics: when a topic winds down it is collapsed into a short \`branch_summary\` on the trunk and the next builds on it, so older context reaches you as summaries. Shifts happen automatically on a detected change of subject; the person can force one with \`/new\`.

A side question can be parked as a checkpoint (\`/checkpoint\`, resume with \`/back\`) and is folded back in as a summary; recent automatic shifts and checkpoints can be reversed with \`/rollback\`.

When a summary is not enough, \`ask_branch\` answers a focused question from any earlier branch's full context.

${referencePointer(import.meta.dirname, "branches")}`;

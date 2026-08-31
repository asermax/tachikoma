import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for skill evolution, injected into the main session's context. Scoped to
 * main: the pass analyzes the trunk's topic branches and its proposals surface in the main
 * conversation — background task runs neither have branches to analyze nor a say in review.
 */
export const SKILL_EVOLUTION_USAGE = `## Skill Evolution

Your skills improve on their own overnight: at trunk close the day's topic branches are analyzed for skill friction, and recurring patterns become proposal branches pushed to origin for the person to review — never silent edits to live skills. Evidence accumulates as pattern pages under \`memories/skill-evolution/\`, maintained by the same pass.

Verified proposals are reported by a dispatched background task (notification-only by default); merging is the person's call — a proposal is just a git branch.

${referencePointer(import.meta.dirname, "skill-evolution")}`;

import type { BranchRecord } from "../../sessions/trunk.ts";

/**
 * The skill-evolution prompt sections (DLT-080 S5): policy-as-prompt-sections like memory's
 * `prompts.ts` — the store conventions (R4/R6), the silent-background contract for analysis forks,
 * and the two composed prompts (per-branch analysis instruction, maintenance system prompt).
 * Changing store policy is a one-line diff here, not a code change (DES-002 style rules).
 */

/**
 * The shape of `memories/skill-evolution/` — shared by every agent that touches the store, so the
 * analysis forks and the maintenance pass enforce one set of conventions (R4/R6): one-line index
 * entries in the PROBLEM — ROOT CAUSE — FIX form, per-pattern pages with Problem / Root cause /
 * Fix / dated Evidence, update-not-duplicate, ~50-line caps, and writes confined to the store.
 */
const STORE_CONVENTIONS_SECTION = `## Store Conventions

The skill-evolution store lives under \`$WORKSPACE/memories/skill-evolution/\` and mirrors the memory wiki conventions:

- **\`MEMORY.md\` is the index**: exactly one line per pattern page, in the form
  \`- [Title](./pattern-slug.md): PROBLEM — ROOT CAUSE — FIX\` — each part a short phrase naming the
  symptom, the underlying cause, and the change that fixes it. Add the line when a page is created,
  update it when the pattern's Problem/Root cause/Fix changes, remove it when the page goes away.
- **One page per pattern** (\`<pattern-slug>.md\`, named for the skill problem it records — broad and
  future-mergeable, never incident- or date-scoped) with four sections:
  - \`## Problem\` — the observable symptom in conversations (e.g. "deploys fail on the flag the skill omits").
  - \`## Root cause\` — the underlying gap in the skill's guidance that produces the symptom.
  - \`## Fix\` — the change the skill needs (what a proposal will eventually author).
  - \`## Evidence\` — dated observations, one bullet per occurrence.
- **Update, never duplicate**: before creating a page, read the index and the existing pages. If a
  page already covers the pattern, fold the new evidence into it — add a dated bullet under
  \`## Evidence\` and refine Problem/Root cause/Fix — instead of creating a sibling. Only create a
  new page when NO existing page covers the pattern.
- **Keep pages under ~50 lines**: summarize evidence rather than accumulating every occurrence, and
  keep Problem/Root cause/Fix to a few lines each.
- **Empty-then-delete**: you have no delete tool. When a page must go away (merged into another,
  entirely superseded), overwrite it with empty content and remove its index line — emptied files
  are cleaned up automatically after you finish.
- **Never read or write \`skill-impact-log.md\`**: the impact ledger is host-written bookkeeping
  (git-derived facts only); it is not store content and no agent maintains it.
- Only create or modify files within \`$WORKSPACE/memories/skill-evolution/\` — never the skills
  themselves, and never any other memory store.`;

/**
 * The analysis fork keeps its persona and full history (fork-continue), so — exactly like memory's
 * extraction forks — it must be told this is background file maintenance, not a conversational
 * turn: no chat reply, no messaging/notification/task tools (the \`FILE_EDIT_TOOLS\` allowlist
 * already bars the tools; this bars the behavior).
 */
const SILENT_BACKGROUND_SECTION = `## Background Maintenance Step

This is a SILENT background skill-analysis step, not part of the conversation. Do NOT send any
user-facing message, ask any question, or produce a chat reply, and do NOT use any messaging,
notification, or task-management tools. Only read files in the workspace and create or modify files
under $WORKSPACE/memories/skill-evolution/. When you are done, simply stop — your only output is the
file changes.`;

/** `{date}` is replaced with the trunk's own day — not wall-clock — so a late close still dates evidence under the day the conversation happened. */
const BRANCH_ANALYSIS_BASE_PROMPT = `We just finished the conversation above. Using what you already know from it, collect this branch's skill-usage evidence — how the assistant's skills performed — and record it in the skill-evolution store.

Today's date is {date}.

## What to collect

Reflect on the conversation — the tool activity and detours, not just the prose; that is where skill failures and workarounds actually show — and collect skill-usage data points:

- **Invoked** — which skills were used, and whether each served the conversation well.
- **Failed** — a skill was invoked and did not work: an error, a wrong result, a dead path.
- **Misapplied** — a skill was used where it did not fit, or its guidance led the assistant astray.
- **Workaround** — the assistant improvised around a gap: manual steps a skill should have covered, corrections after following stale guidance, knowledge it lacked and had to reconstruct.

Only friction that a skill change could fix belongs in this store. Record what a skill got wrong or omitted — the skill it indicts, what happened, and what the skill should have said or done — not a log of successful routine use.

## How to work

1. **Read the current workspace skills** under \`$WORKSPACE/skills/\` (each skill's \`SKILL.md\`). The evidence is about these skills: a pattern must name the skill it indicts, and you need to know what the skill already covers to judge what is missing or wrong.
2. **Read the store** — \`$WORKSPACE/memories/skill-evolution/MEMORY.md\` and the existing pattern pages — before writing anything.
3. **Record each pattern** per the conventions below: update the matching page when one already covers it (dated evidence bullet; refined Problem/Root cause/Fix), or create the page and its index line when none does.
4. **Date every evidence entry \`{date}\`**, citing the branch id — e.g. \`- {date} (topic-2): <what happened>\` — so recurrence across nights is visible.
5. A conversation where skills were used without incident may legitimately yield **nothing to record**. Recording nothing is a clean outcome, not a failure — never invent patterns to fill the store.`;

const MAINTENANCE_BASE_PROMPT = `You are a skill-evolution store maintenance agent performing pattern-page cleanup.

## Directory

\`$WORKSPACE/memories/skill-evolution/\`

## Pre-check

If the directory holds no pattern pages (only \`MEMORY.md\` and \`skill-impact-log.md\`, or nothing at all), stop immediately — nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process \`.md\` files other than \`MEMORY.md\` and \`skill-impact-log.md\`.

## Evaluation Criteria

Read every pattern page and evaluate it for these issues:

### Near-duplicate patterns

Two pages describe the same underlying skill problem — the same root cause, or one fix would resolve both — possibly worded differently or filed under different slugs:

- Merge them into ONE page: keep the clearer Problem/Root cause/Fix, and combine both Evidence sections, deduplicating identical observations.
- Empty the merged-away page and remove its index line; keep the surviving page's line in sync with the merged content.

### Size enforcement

Flag any page exceeding ~50 lines for consolidation:

- Summarize the Evidence section — the pattern's history needs the dates and the shape of recurrence, not every occurrence verbatim.
- Tighten Problem/Root cause/Fix to a few lines each.

### Stale structure

- Repair a page missing the Problem / Root cause / Fix / Evidence structure: restore the headers and place content under the right one.
- A page whose entire content is superseded — the current \`SKILL.md\` under \`$WORKSPACE/skills/\` shows the guidance was fixed, so the pattern no longer applies — is emptied, with its index line removed.

## Index consistency

Every surviving pattern page has exactly one \`MEMORY.md\` line in the \`- [Title](./pattern-slug.md): PROBLEM — ROOT CAUSE — FIX\` form; merged-away and emptied pages have none.

## Idempotency

If no changes are needed, exit with no changes.`;

/**
 * The follow-up user instruction handed to the analysis fork of one topic branch (S5). The fork
 * carries the branch's turns live — the same assistant that had the conversation reflects on it —
 * so the instruction only supplies the task. The per-branch suffix stamps the trunk's day and the
 * branch id (memory's `branchStoreInstruction` idiom): the fork must focus on this branch's own
 * turns and date its evidence with the day the conversation happened.
 */
export const branchAnalysisInstruction = (
  workspaceRoot: string,
  record: BranchRecord,
  day: string,
): string =>
  `${[BRANCH_ANALYSIS_BASE_PROMPT, STORE_CONVENTIONS_SECTION, SILENT_BACKGROUND_SECTION]
    .join("\n\n")
    .replaceAll("$WORKSPACE", workspaceRoot)
    .replaceAll(
      "{date}",
      day,
    )}\n\nThis conversation is a single topic branch (\`${record.branchId}\`) from the ${day} session. Focus only on this branch's own turns.`;

/**
 * The system prompt for the in-run maintenance pass (R6b) — a context-free headless run, so unlike
 * the analysis fork it needs the full task statement inline. Sync: unlike memory's maintenance
 * prompts it builds no cross-store manifest, so there is nothing to await.
 */
export const maintenanceSystemPrompt = (workspaceRoot: string): string =>
  [MAINTENANCE_BASE_PROMPT, STORE_CONVENTIONS_SECTION]
    .join("\n\n")
    .replaceAll("$WORKSPACE", workspaceRoot);

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
  - \`## Fix\` — the change the skill needs (what a proposal will eventually author: adding,
    correcting, removing, or consolidating guidance — up to retiring the skill entirely).
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
- **Workaround** — the assistant improvised around a gap: manual steps a skill should have covered, knowledge it lacked and had to reconstruct.
- **Stale** — the skill's guidance no longer matches reality: commands, paths, steps, or behaviors that have changed or disappeared, so following it needed correcting or skipping.
- **Redundant** — the skill carries near-duplicate or contradictory guidance — visible reading the skill, or in the confusion it caused.
- **Obsolete** — a skill, or a workflow within it, whose purpose has disappeared: the workflow it describes no longer exists or was replaced, so its fix may be removal rather than repair.

Only friction that a skill change could fix belongs in this store. Record what a skill got wrong, omitted, or outlived — the skill it indicts, what happened, and the change it needs — not a log of successful routine use.

## How to work

1. **Read the current workspace skills** under \`$WORKSPACE/skills/\` (each skill's \`SKILL.md\`). The evidence is about these skills: a pattern must name the skill it indicts, and you need to know what the skill already covers to judge what is missing, wrong, or no longer worth keeping.
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
- A page whose entire content is superseded — the current \`SKILL.md\` under \`$WORKSPACE/skills/\` shows the guidance was fixed, or the skill no longer exists, so the pattern no longer applies — is emptied, with its index line removed.

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

/**
 * The two built-in authoring guides, force-loaded into the proposal run as actual skills (the
 * `skillPaths`/`forceLoadSkills` wiring in `runProposalAgent`). Exported because the rule text
 * below and the run wiring key on the same names — a guide rename is one edit here, and the rule
 * stays conditional on the guides' presence so fail-soft loading and policy never disagree.
 */
export const AUTHORING_GUIDE_SKILLS = ["skill-authoring", "workflow-authoring"] as const;

const guideNamesPhrase = AUTHORING_GUIDE_SKILLS.map((name) => `\`${name}\``).join(" and ");

/**
 * The proposal agent's authoring protocol (S7, R7–R9/R14): the worktree cut/push workflow, the
 * one-branch-per-pattern rule, the branch naming rule, and the workspace-skills-only constraint.
 * The prompt-shaped rules mirror the tool surface's mechanical refusals — a violated rule comes
 * back as instructive text the agent self-corrects within the same run (the collision-suffix rule
 * executes itself: push refuses an existing name, the agent retries with `-2`).
 */
const PROPOSAL_BASE_PROMPT = `You are the skill-evolution proposal agent. You turn accumulated skill-usage evidence into reviewable, branch-based proposals. Nothing is ever applied to the live skills: every change is authored in a temporary worktree, committed, and pushed as a new branch for the user to review.

## Ground rules

- **Workspace skills only** (R14): propose only changes under a worktree's \`skills/\` directory. Each workspace skill is a directory under \`$WORKSPACE/skills/\` containing a \`SKILL.md\`; built-in skills are not part of this repository and are out of scope. Never touch anything outside a worktree's \`skills/\` directory.
- **Follow the authoring guides** when their skill content is present in your input (force-loaded ${guideNamesPhrase}): every file you create or edit under the worktree's \`skills/\` must conform to them. A new \`skills/<name>/SKILL.md\` follows the full conventions (frontmatter with a trigger-rich description, the documented section shapes, references where they help); a new workflow uses the numbered step directories with \`title\`-frontmattered \`instructions.md\` and is documented in its skill's \`SKILL.md\`; edits preserve the skill's established structure, and removals leave the surviving skill coherent — documentation of a removed workflow goes with it, and cross-references stay valid. Where the guides speak of the workspace's \`skills/\` directory they mean the current proposal worktree's \`skills/\`. The guides describe runtime workflow and delegation tools that are NOT part of your tool surface — treat them as authoring conventions only. The guides are bundled reference material, never proposal targets.
- **One branch per pattern**: each pattern you act on gets exactly one proposal on its own branch — a change to the existing skill's files (adding, correcting, removing, or consolidating its guidance), a new \`skills/<name>/SKILL.md\` for a recurring workflow that has no skill yet, or deletion of the skill's entire directory when the skill no longer serves a purpose (R7). If the skill a pattern indicts existed and is gone from the worktree (deleted on a merged proposal), decline that pattern — propose nothing for it.
- **Branch naming** (R9): \`skill-evolution/<skill>-<slug>\` — the skill directory name, a hyphen, and a short lowercase slug (lowercase letters, digits, single hyphens, starting with a letter or digit). Example: \`skill-evolution/deploy-add-env-flag\`. A name that already exists on the remote is refused on push — pick a fresh one (e.g. append \`-2\`).
- **Never the default branch, never force**: push only ever creates a brand-new \`skill-evolution/*\` branch; the default branch ($DEFAULT_BRANCH) and any history rewrite are refused.

## Worktree protocol (one pass per proposal)

1. Cut the worktree from the remote default branch: \`git\` with args
   \`["worktree", "add", "-b", "<branch>", "<worktree-path>", "refs/remotes/origin/$DEFAULT_BRANCH"]\`
   where \`<worktree-path>\` is a NEW directory under \`$TMPDIR\` (e.g. \`$TMPDIR/<slug>\`). Work only
   inside worktrees under \`$TMPDIR\` — the live working tree at \`$WORKSPACE\` is never touched.
2. Read the skill files in the worktree (\`read_file\`/\`list_dir\` with that worktree's paths) and make
   the change — \`write_file\` for edits and new files, \`delete_path\` for removals (a whole-skill
   removal deletes the \`skills/<name>/\` directory). Read before you write: the proposal should read
   as a considered change to the skill's actual guidance. Deletions stage with the same \`add\`:
   \`git add\` records removals, not just modifications.
3. Stage and commit inside the worktree — \`add\`/\`commit\` are refused unless git's \`path\` parameter
   names the worktree (or a directory inside it): \`git\` with args \`["add", "<paths>"]\` and \`path\`
   set to the worktree directory, then \`["commit", "-m", "<imperative message under 72 chars>"]\`
   the same way. Stage only the files you changed.
4. Publish: \`git\` with args \`["push", "origin", "<branch>"]\` (again with \`path\` set to the worktree).
   A refused push carries the reason in its text — fix the name or the state and retry.
5. You may remove a finished worktree (\`["worktree", "remove", "<path>"]\`) or leave it; the host
   sweeps every worktree and local branch when the run ends.

## Finishing

When every proposal is pushed — or you conclude none is worth making — call \`report_proposals\`
ONCE with every proposal: \`{branch, skill, pattern, description}\` (\`skill\` = the skill directory
name, \`pattern\` = the pattern page filename exactly as given in the task prompt, \`description\` =
one line on what the change does). That call is the required terminal step; after it, stop.

Making no proposal at all is a legitimate outcome — report an empty list rather than inventing
work. The host verifies everything from git state alone; your report by itself records nothing.`;

/**
 * The system prompt for the proposal run (S7): the authoring protocol bound to this workspace's
 * tmp dir and default branch. Sync like the maintenance prompt — plain string substitution, no
 * manifest to await.
 */
export const proposalSystemPrompt = (
  workspaceRoot: string,
  tmpDir: string,
  defaultBranch: string,
): string =>
  PROPOSAL_BASE_PROMPT.replaceAll("$WORKSPACE", workspaceRoot)
    .replaceAll("$TMPDIR", tmpDir)
    .replaceAll("$DEFAULT_BRANCH", defaultBranch);

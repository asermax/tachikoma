import { readdir } from "node:fs/promises";
import { join } from "node:path";

import { FILE_EDIT_TOOLS } from "../../agent/file-tools.ts";
import type { Logger } from "../../log.ts";
import type { Runner } from "./extraction.ts";
import {
  fileExists,
  isIndexedStore,
  MEMORY_STORES,
  type MemoryStore,
  storeDir,
  sweepEmptyMarkdown,
} from "./layout.ts";
import { INDEX_LIGHT_MAINTENANCE_SECTION, STORE_PURPOSE_SECTION, scopeSection } from "./prompts.ts";

export interface MaintenanceThresholds {
  recentDays: number;
  weeklyThresholdMonths: number;
  monthlyThresholdMonths: number;
}

export interface MaintenanceDeps {
  side: Runner;
  workspaceRoot: string;
  settings: MaintenanceThresholds;
  log: Logger;
  /** Injectable clock for tests — Sunday triggers the full index rebuild. */
  now?: () => Date;
  /**
   * Commit the files the maintenance pass touched. Maintenance runs detached from
   * any session, so without this its edits would sit uncommitted until the next
   * session close. Omitted in tests so the pass doesn't reach for a git repo.
   */
  commitChanges?: (message: string) => Promise<void>;
}

const episodicMaintenancePrompt = ({
  recentDays,
  weeklyThresholdMonths,
  monthlyThresholdMonths,
}: MaintenanceThresholds): string => `You are a memory maintenance agent performing episodic memory consolidation.

## Directory

\`$WORKSPACE/memories/episodic/\`

## Pre-check

If the directory is empty or contains no \`.md\` files, stop immediately — nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with \`.md\` extension.
- If a file's content looks truncated or garbled, skip it.

## Time Tiers

Categorize every non-skipped file by its date (extracted from filename \`YYYY-MM-DD.md\` or from \`YYYY-WNN.md\` / \`YYYY-MM.md\` summary files):

### Tier 1: Recent (last ${recentDays} days) — Clean only

- **Goal**: Reduce verbosity without losing substance.
- Remove repeated information that appears identically across entries.
- Remove excessive implementation detail (file lists, step-by-step code changes, routine activity status).
- Preserve: outcomes, decisions, new information, significant events.
- **NEVER delete files or remove substantive content from this tier.**
- If an entry is already clean and concise, leave it unchanged.

### Tier 2: Weekly consolidation (${recentDays} days – ${weeklyThresholdMonths} months) — Weekly summaries

- Group daily files by ISO week (e.g., files from 2026-04-13 through 2026-04-19 all belong to week 2026-W15).
- For each group, create a weekly summary file named \`YYYY-WNN.md\` (e.g., \`2026-W15.md\`).
- The summary should capture the high-level narrative and significant events of that week — decisions, outcomes, notable activities.
- Discard: routine implementation details, file change lists, repetitive status updates, step-by-step technical minutiae.
- If a weekly summary \`YYYY-WNN.md\` already exists from a previous run, merge new content into it — do not overwrite.
- After successful consolidation, delete the original daily files that were consolidated.
- Partial groups (e.g., only 2 days in a week) are consolidated into a single summary for that period.

### Tier 3: Monthly consolidation (${weeklyThresholdMonths} months – ${monthlyThresholdMonths} months) — Monthly summaries

- Group all files (daily or weekly) by month.
- For each group, create a monthly summary file named \`YYYY-MM.md\` (e.g., \`2026-04.md\`).
- The summary should capture the most significant themes and events of that month at a high level.
- Discard: all routine detail — keep only notable outcomes, key decisions, and significant changes.
- If a monthly summary \`YYYY-MM.md\` already exists from a previous run, merge new content into it — do not overwrite.
- After successful consolidation, delete the original files.
- Partial months are consolidated into a single summary.

### Tier 4: Older than ${monthlyThresholdMonths} months — Delete

- Delete all files older than ${monthlyThresholdMonths} months.
- These entries have been through monthly consolidation already — anything remaining at this age is beyond the retention window.

## Idempotency

Before acting, check whether the work has already been done:
- If a weekly/monthly summary already exists and covers the files in its range, do not recreate it.
- If daily files in Tier 1 are already clean and concise, do not re-edit.
- If no files need processing, exit with no changes.`;

const TOPICS_MAINTENANCE_PROMPT = `You are a memory maintenance agent performing topics memory cleanup.

## Directory

\`$WORKSPACE/memories/topics/\`

## Pre-check

If the directory is empty or contains no \`.md\` files, stop immediately — nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with \`.md\` extension.

## Evaluation Criteria

Read all topic files and evaluate each for these issues. A topic file holds everything known about a subject — both stable reference facts and the user's subjective preferences — so evaluate the file as a whole, not by signal type.

### Staleness

An entry is stale when it describes a state that is no longer accurate:
- References to past dates or completed projects (e.g., "currently working on X" when X shipped months ago).
- Information contradicted by newer entries.
- Technical details that reference outdated tools, versions, or configurations.
- Reversed preferences (e.g., file says "prefers dark mode" but a newer entry says the user switched to light mode).

For stale entries:
- If the entry has a newer, accurate replacement: remove the stale version.
- If the entry can be updated to reflect current state: edit it.
- If the entry describes a completed event with no ongoing relevance: remove it.

### Redundancy

Same information stated in different files or restated within a file:
- Keep the most complete and well-organized version.
- Remove the duplicate entries.

### Overlap

Related subjects split across multiple files:
- When files cover overlapping subject areas, merge them into a single consolidated file.
- Choose the best filename from the originals, or create a more descriptive one.
- Remove the original files after merging.

#### Cluster Consolidation

Beyond pairwise overlap, look for clusters of many small files that fragment a single broad subject into per-incident, per-bug, or per-date entries — this is the main driver of topics directory bloat:

- List all files in \`$WORKSPACE/memories/topics/\` and group them by shared prefix or core subject (project, system, tool, or domain).
- When 3 or more files share the same prefix or core subject, treat them as a cluster and consolidate aggressively:
  - Merge all of the cluster's content into a single broad-topic file named \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, or \`<domain>.md\` — the same broad-topic convention used at extraction time.
  - If a broad-topic file already exists in the cluster, merge the others into it. Otherwise, pick a broad name and create the consolidated file.
  - Delete the original narrow files after merging.
- Generic examples of clusters to consolidate (patterns, not literal names): files matching \`<project>-<bug-description>-<YYYY-MM-DD>.md\`, \`<project>-patch-<issue-id>.md\`, \`<system>-incident-<date>.md\`, \`<topic>-session-<date>.md\` — all of these should fold into the broad \`<project>.md\`, \`<system>.md\`, or \`<topic>.md\` file.
- Preserve substantive content during the merge — both the reference facts and the preferences; deduplicate restated information and discard incidental detail that does not belong in durable topics.

### Size Enforcement

Flag any file exceeding ~50 lines for consolidation or pruning:
- If a file exceeds ~50 lines, it likely contains implementation details, narrative, or one-time records that belong in project docs or episodic memory rather than durable topics.
- Prune implementation details and transient information.
- Consolidate related entries within the file to eliminate restatement.

### Context File Overlap

Check whether topic content is already covered by the context files at the workspace root:
- Read \`$WORKSPACE/USER.md\` and \`$WORKSPACE/AGENTS.md\` for the same subject.
- If a context file already captures the information, trim the topic file to a brief reference (e.g., "See USER.md for details about X").
- Context files are less authoritative than topics for their respective subjects; topics supplement and detail, they should not duplicate context-file summaries.

## Deletion

- If a topic file is entirely obsolete (all entries stale, no useful content), delete it.
- Do NOT delete files that contain any useful or current information — a file holding even one current preference or fact should be kept.

## Idempotency

The goal is to leave the store materially smaller and more consolidated than you found it. Treat absence of exact duplicates as insufficient grounds for skipping — semantic overlap and per-incident granularity are also valid triggers for action.

### Consolidate narrow per-incident files into broader topic files

When you see multiple narrow files about the same subject (per-date, per-incident, per-PR, per-bug), merge them into a single broader topic file. Example: \`<project>-bug-<YYYY-MM-DD>.md\`, \`<project>-process.md\`, and \`<project>-rollout-notes.md\` should all be folded into \`<project>.md\`. Keep the most durable, generally-useful information; discard the incident-specific noise. Delete the merged-from files after consolidation.

### Aggressively prune resolved incidents

Files whose content carries headers like "Status: Completed", "Status: Merged", "Status: Resolved", or "Status: Fixed" describe one-time events — that content belongs in episodic memory, not topics. Delete these files outright. If a topic file mixes durable content with a resolved-incident section, strip the resolved section and keep the rest.

If no changes are needed, exit with no changes.`;

const LEARNINGS_MAINTENANCE_PROMPT = `You are a memory maintenance agent performing learnings memory cleanup.

## Directory

\`$WORKSPACE/memories/learnings/\`

## Pre-check

If the directory is empty or contains no \`.md\` files, stop immediately — nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with \`.md\` extension.

## File Model

Each learnings file holds one theme of recurring friction under two sections:
- \`## Drafts\` — tentative lessons observed once (not yet corroborated as recurring).
- \`## Confirmed\` — the same friction, now corroborated across sessions (permanent).

A single file may hold entries in either or both sections. Keep this two-section structure intact. If a file is missing the headers or scrambles entries across them, repair the structure in place: restore the \`## Drafts\` / \`## Confirmed\` headers and place each entry under the right one. If a file's structure is unrecoverable, leave it for the next pass rather than guessing.

## Evaluation Criteria

Read all learnings files and evaluate each for these issues. A learning is experience (what bites, what worked), not knowledge — evaluate it as a recurring friction, not as a topic to keep tidy.

### Draft Promotion (safety-net)

Review every \`## Drafts\` entry. Promote any draft that is clearly corroborated as a recurring pattern — the same friction appears in more than one entry, or a draft and a confirmed entry describe the same recurring problem — by moving it from \`## Drafts\` to \`## Confirmed\`. This is the safety-net promotion: extraction usually promotes on recognized recurrence, but a missed re-read is caught here. Leave genuinely single-occurrence drafts as drafts: a draft is not deleted merely for being seen once (only stale or contradicted learnings are pruned).

### Contradiction — a resolved friction is corrected or removed, never promoted

A draft or confirmed entry is contradicted when a later observation shows the friction was **resolved**: the flaky test now passes, the deploy step no longer fails, the user confirms the constraint was lifted, the dead-end approach was abandoned for a reason that no longer applies. When that happens, correct the entry to reflect the resolution or remove it entirely — never promote a resolved friction, because resolution is the opposite of recurrence.

### Staleness

An entry is stale when the friction it describes no longer applies or has been superseded:
- References to resolved issues, fixed bugs, or retired tooling.
- Information contradicted by a newer entry.
- Friction about a project, system, or workflow that no longer exists.

For stale entries: update if the friction still applies but its details changed, or remove if the friction is fully resolved or obsolete.

### Redundancy

Same friction stated in different files or restated within a file:
- Keep the most complete and well-organized version.
- Remove the duplicate entries.

### Fragmentation — consolidate per-incident files into broad slug files

Beyond pairwise overlap, look for clusters of many small files that fragment a single friction theme into per-incident, per-bug, or per-date entries — the main driver of learnings directory bloat and the pattern this store forbids:

- List all files in \`$WORKSPACE/memories/learnings/\` and group them by the friction theme or subject (project, system, tool, or kind of problem).
- When multiple files share the same theme, consolidate them into a single broad-theme file named for the friction (\`<theme>.md\`, e.g. \`test-suite.md\`, \`deploys.md\`, \`<tool>.md\`) — broad and future-mergeable, never incident- or date-scoped.
- Merge their drafts and confirmed entries (deduplicating), then empty the original narrow files so they are cleaned up.
- Preserve substantive lessons during the merge; discard incident-specific noise.

### Size Enforcement

Keep files concise — flag any file exceeding ~50 lines for consolidation or pruning. A learnings file that long likely bundles several distinct frictions (split them into their own broad slug files) or carries narrative that belongs in episodic memory rather than a learning.

### Topic Orthogonality

Learnings hold experience and sit orthogonal to the \`Skills > Topics > Context\` authority hierarchy. A learning and a topic about the same subject are different kinds of information, so never merge a learning into a topic file, prune a learning in deference to a topic, or restate a topic as a learning. The cross-store visibility section lists topics only so you can avoid restating them — not so you defer to them.

## Deletion

- If a learnings file is entirely obsolete (all entries resolved or stale, no useful content), delete it.
- Do NOT delete files that contain any useful, current lesson — a file holding even one current draft or confirmed entry should be kept.

## Idempotency

The goal is to leave the store materially smaller and better-curated than you found it. Treat absence of exact duplicates as insufficient grounds for skipping — semantic overlap, resolved friction, and per-incident granularity are also valid triggers for action.

If no changes are needed, exit with no changes.`;

const heavyIndexRebuildSection = (store: MemoryStore): string => `## Memory Index Rebuild (full)

Rebuild the MEMORY.md index file in \`$WORKSPACE/memories/${store}/\` from scratch:

1. **List all files**: find all \`.md\` files in \`$WORKSPACE/memories/${store}/\` (exclude \`MEMORY.md\` itself).

2. **If the directory is empty** (no \`.md\` files besides MEMORY.md): write MEMORY.md with only the header \`# Memory Index\` and stop — no further steps needed.

3. **Describe every file**: read each file and produce a concise one-line description (under 80 characters) that captures the file's topic and scope. Focus on WHAT the file contains, not its history.

4. **Analyze for structural improvements**:
   - If multiple files have very similar content (same topic/domain), consider merging them into a single file with a broad name.
   - If a file's name doesn't match its content (e.g., date-based name for general content), rename it: write the content to the new name, then empty the old file.
   - If files are fragmented (many small files about the same thing), consolidate into fewer, broader files.

5. **Write MEMORY.md from scratch** with:
   - Header: \`# Memory Index\`
   - One entry per current file in format: \`[Human-readable Name](./filename.md): One-line description\`
   - Entries listed in alphabetical order by filename

### Entry Format

\`\`\`
[Topic Name](./topic-slug.md): One-line description of what this file contains
\`\`\`

- The name in brackets is a human-readable topic name (Title Case)
- The path is a relative markdown link starting with \`./\`
- The description is one line, under 80 characters
- Separate the link and description with \`: \` (colon + space)`;

const CONTRADICTION_DETECTION_SECTION = `## Cross-Store Contradiction Detection

When reviewing files in this store, check for contradictions against other stores:

1. Read the files listed in the cross-store visibility section above
2. Compare their content against the files you're maintaining in this store
3. If you find contradictory information:
   - Determine which store is more authoritative per the authority hierarchy (Skills > Topics > Context)
   - If this store is LESS authoritative: update or remove the contradicting entry in this store to match the more authoritative source
   - If this store is MORE authoritative: leave this store's entry unchanged (the other store's maintenance tick will handle it)
4. When information in this store duplicates detail from a more authoritative store: trim this store's entry to a brief pointer (e.g., "See memories/topics/X.md for details")`;

const STORE_LABELS: Record<MemoryStore, string> = {
  episodic: "Episodic Files",
  topics: "Topics Files",
  learnings: "Learnings Files",
};

const CONTEXT_FILE_NAMES = ["SOUL.md", "USER.md", "AGENTS.md"];

const CONTEXT_MAINTENANCE_PROMPT = `You are a memory maintenance agent performing foundational context file cleanup.

## Files

You are responsible for three foundational context files at the workspace root:
- \`$WORKSPACE/SOUL.md\` — Personality traits, tone, and behavioral guidelines
- \`$WORKSPACE/USER.md\` — What the assistant knows about the user
- \`$WORKSPACE/AGENTS.md\` — Operational instructions and workflow preferences

## Pre-check

If none of the three files exist, stop immediately — nothing to maintain.

## Evaluation Criteria

Read all three context files and evaluate each for these issues:

### Staleness

An entry is stale when it describes a state that is no longer accurate:
- References to completed projects or resolved issues — confirm against actual workspace state (read project directories, check file existence) before removing
- Outdated role information, past events, or time-specific entries that are no longer relevant
- Technical details that reference outdated tools, versions, or configurations
- Entries about resolved bugs or completed work — the fix is done, the instruction is no longer needed

For stale entries:
- Remove the entry entirely if it has no ongoing relevance
- If the entry can be updated to reflect current state, edit it
- Do NOT prune based on vague hints, assumptions, or age alone — only remove when you have clear evidence (e.g., the project directory no longer exists, the referenced file has been deleted, the tool version has changed)

### Redundancy

Same information stated multiple times within or across files:
- Keep the most complete and well-organized version
- Remove the duplicate entries

### Overlap

Related topics split across sections within the same file:
- When two sections cover the same topic with semantically equivalent content, merge them into one section combining the best of both
- Only consolidate when sections are truly equivalent — related-but-distinct topics must remain separate

## Size Limits

Enforce these size limits by pruning actively:
- **USER.md** must stay under ~120 lines. When it exceeds the limit: summarize verbose sections, remove stale sections, or omit details that belong in topic memory.
- **AGENTS.md** must stay under ~400 lines. When it exceeds the limit: remove entries about resolved bugs or completed work, and consolidate duplicated entries across sections.

## Constraints

- **Cleanup-only**: Do NOT add new content — only clean and consolidate what is already there. Adding new content from conversations is the job of the per-session context update, not this maintenance pass.
- **Read-first**: Always read a file before modifying it.
- **Preserve structure**: Keep existing formatting and organization.
- **Conservative**: Only remove content with clear evidence of staleness.
- **SOUL.md**: Be especially conservative — personality traits and tone guidelines should only be removed when the user has explicitly contradicted them or when they duplicate each other.

## Idempotency

Treat absence of obvious problems as insufficient grounds for skipping — actively re-apply the Evaluation Criteria and Size Limits above. Stale instructions added for resolved incidents and entries that belong in topic memory accumulate quietly between runs and won't always surface in a quick scan.

If no changes are needed, exit with no changes.

## Scope

You can read files anywhere in the workspace (needed to validate claims against actual project state). Only modify \`$WORKSPACE/SOUL.md\`, \`$WORKSPACE/USER.md\`, and \`$WORKSPACE/AGENTS.md\`. Do not create, delete, or modify any other files.`;

/**
 * Build a `### <Label>` names-only bullet section for each store passing `include`,
 * skipping stores whose dir is missing (ENOENT) or holds no markdown files.
 */
const buildStoreSections = async (
  workspaceRoot: string,
  include: (store: MemoryStore) => boolean,
): Promise<string[]> => {
  const sections: string[] = [];

  for (const store of MEMORY_STORES) {
    if (!include(store)) continue;

    let files: string[];
    try {
      files = (await readdir(storeDir(workspaceRoot, store)))
        .filter((name) => name.endsWith(".md"))
        .sort();
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;

      continue;
    }

    if (files.length === 0) continue;

    sections.push(
      [
        `### ${STORE_LABELS[store]}`,
        "",
        ...files.map((name) => `- \`memories/${store}/${name}\``),
      ].join("\n"),
    );
  }

  return sections;
};

/** Names-only listing of the other stores so the maintenance agent can reconcile across them. */
export const buildCrossStoreManifest = async (
  workspaceRoot: string,
  current: MemoryStore,
): Promise<string | null> => {
  const sections = await buildStoreSections(workspaceRoot, (store) => store !== current);

  const contextFiles: string[] = [];
  for (const name of CONTEXT_FILE_NAMES) {
    if (await fileExists(join(workspaceRoot, name))) contextFiles.push(name);
  }

  if (contextFiles.length > 0) {
    sections.push(
      [
        "### Context Files",
        "",
        ...contextFiles.map((name) => `- \`${name}\` (workspace root)`),
      ].join("\n"),
    );
  }

  if (sections.length === 0) return null;

  return [
    "## Cross-Store Visibility",
    "",
    "Files in other information stores (names and paths only, not content):",
    "",
    sections.join("\n\n"),
    "",
    "If a section in this store duplicates or contradicts content in a listed file, reconcile per the authority hierarchy in your instructions.",
  ].join("\n");
};

// Each store's base maintenance prompt. Keyed on MemoryStore so a store missing an entry is a
// compile error, not a silent fall-through to topics.
const MAINTENANCE_PROMPTS: Record<MemoryStore, (settings: MaintenanceThresholds) => string> = {
  episodic: episodicMaintenancePrompt,
  topics: () => TOPICS_MAINTENANCE_PROMPT,
  learnings: () => LEARNINGS_MAINTENANCE_PROMPT,
};

const basePrompt = (store: MemoryStore, settings: MaintenanceThresholds): string =>
  MAINTENANCE_PROMPTS[store](settings);

export const maintenanceSystemPrompt = async (
  store: MemoryStore,
  { workspaceRoot, settings, now }: Pick<MaintenanceDeps, "workspaceRoot" | "settings" | "now">,
): Promise<string> => {
  const parts = [basePrompt(store, settings)];

  // Day-of-week dispatch: weekdays keep the index consistent cheaply,
  // Sunday rebuilds it from scratch with fresh descriptions.
  if (isIndexedStore(store)) {
    const isSunday = (now?.() ?? new Date()).getDay() === 0;
    parts.push(isSunday ? heavyIndexRebuildSection(store) : INDEX_LIGHT_MAINTENANCE_SECTION);
  }

  parts.push(STORE_PURPOSE_SECTION);

  const manifest = await buildCrossStoreManifest(workspaceRoot, store);
  if (manifest != null) parts.push(manifest);

  parts.push(CONTRADICTION_DETECTION_SECTION, scopeSection(store));

  return parts.join("\n\n").replaceAll("$WORKSPACE", workspaceRoot);
};

/** Names-only listing of the memory stores so the context tick can reconcile against more authoritative topics. */
export const buildStoreManifestForContext = async (
  workspaceRoot: string,
): Promise<string | null> => {
  const sections = await buildStoreSections(workspaceRoot, () => true);

  if (sections.length === 0) return null;

  return [
    "## Memory Store Visibility",
    "",
    "Files in the memory stores (names and paths only, not content):",
    "",
    sections.join("\n\n"),
    "",
    'When a context-file section duplicates detail already captured in a more authoritative topic file, trim it to a brief pointer (e.g., "See memories/topics/X.md for details") rather than inlining the content.',
  ].join("\n");
};

export const contextMaintenanceSystemPrompt = async (workspaceRoot: string): Promise<string> => {
  const parts = [CONTEXT_MAINTENANCE_PROMPT, STORE_PURPOSE_SECTION];

  const manifest = await buildStoreManifestForContext(workspaceRoot);
  if (manifest != null) parts.push(manifest);

  return parts.join("\n\n").replaceAll("$WORKSPACE", workspaceRoot);
};

/**
 * Periodic cleanup pass over the foundational context files (SOUL/USER/AGENTS).
 * Cleanup-only and conservative: reviews for staleness, redundancy, overlap, and
 * bloat, applying edits in place. New content is the per-session update's job, not this.
 */
export const runContextMaintenanceTick = async (
  deps: Pick<MaintenanceDeps, "side" | "workspaceRoot" | "log" | "commitChanges">,
): Promise<void> => {
  deps.log.info("context maintenance tick started");

  const start = Date.now();

  const result = await deps.side.run({
    tools: FILE_EDIT_TOOLS,
    system: await contextMaintenanceSystemPrompt(deps.workspaceRoot),
    prompt: "Perform the context file cleanup pass now, following your instructions.",
    tier: "processor",
  });

  await deps.commitChanges?.("chore(memory): scheduled context file maintenance");

  deps.log.info(
    { producedOutput: result.text.length > 0, durationMs: Date.now() - start },
    "context maintenance tick completed",
  );
};

/** Daily maintenance pass over one memory store: consolidate, prune, keep indexes in sync. */
export const runMaintenanceTick = async (
  store: MemoryStore,
  deps: MaintenanceDeps,
): Promise<void> => {
  deps.log.info({ store }, "memory maintenance tick started");

  const start = Date.now();

  const result = await deps.side.run({
    tools: FILE_EDIT_TOOLS,
    system: await maintenanceSystemPrompt(store, deps),
    prompt: "Perform the maintenance pass now, following your instructions.",
    tier: "processor",
  });

  await sweepEmptyMarkdown(storeDir(deps.workspaceRoot, store), deps.log);

  await deps.commitChanges?.(`chore(memory): scheduled ${store} maintenance`);

  deps.log.info(
    { store, producedOutput: result.text.length > 0, durationMs: Date.now() - start },
    "memory maintenance tick completed",
  );
};

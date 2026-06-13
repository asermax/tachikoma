import { readdir } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";
import { MEMORY_FILE_TOOLS, type Runner } from "./extraction.ts";
import {
  fileExists,
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

const FACTS_MAINTENANCE_PROMPT = `You are a memory maintenance agent performing facts memory cleanup.

## Directory

\`$WORKSPACE/memories/facts/\`

## Pre-check

If the directory is empty or contains no \`.md\` files, stop immediately — nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with \`.md\` extension.

## Evaluation Criteria

Read all fact files and evaluate each for three issues:

### Staleness

An entry is stale when it describes a state that is no longer accurate:
- References to past dates or completed projects (e.g., "currently working on X" when X shipped months ago).
- Information contradicted by newer entries.
- Technical details that reference outdated tools, versions, or configurations.

For stale entries:
- If the entry has a newer, accurate replacement: remove the stale version.
- If the entry can be updated to reflect current state: edit it.
- If the entry describes a completed event with no ongoing relevance: remove it.

### Redundancy

Same information stated in different files:
- Keep the most complete and well-organized version.
- Remove the duplicate entries.

### Overlap

Related topics split across multiple files:
- When files cover overlapping subject areas, merge them into a single consolidated file.
- Choose the best filename from the originals, or create a more descriptive one.
- Remove the original files after merging.

#### Cluster Consolidation

Beyond pairwise overlap, look for clusters of many small files that fragment a single broad topic into per-incident, per-bug, or per-date entries — this is the main driver of facts directory bloat:

- List all files in \`$WORKSPACE/memories/facts/\` and group them by shared prefix or core topic (project, system, tool, or domain).
- When 3 or more files share the same prefix or core topic, treat them as a cluster and consolidate aggressively:
  - Merge all of the cluster's content into a single broad-topic file named \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, or \`<domain>.md\` — the same broad-topic convention used at extraction time.
  - If a broad-topic file already exists in the cluster, merge the others into it. Otherwise, pick a broad name and create the consolidated file.
  - Delete the original narrow files after merging.
- Generic examples of clusters to consolidate (patterns, not literal names): files matching \`<project>-<bug-description>-<YYYY-MM-DD>.md\`, \`<project>-patch-<issue-id>.md\`, \`<system>-<incident>-<date>.md\`, \`<topic>-session-<date>.md\` — all of these should fold into the broad \`<project>.md\`, \`<system>.md\`, or \`<topic>.md\` file.
- Preserve substantive content during the merge; deduplicate restated information and discard incidental detail that does not belong in stable reference facts.

### Size Enforcement

Flag any file exceeding 40 lines for consolidation:
- If a file exceeds 40 lines, it likely contains implementation details or narrative that belongs in project docs or episodic memory rather than stable reference facts
- Prune implementation details and transient information
- Consolidate related entries within the file to eliminate restatement

### Context File Overlap

Check whether facts are already covered by the context files at the workspace root:
- Read \`$WORKSPACE/USER.md\` and \`$WORKSPACE/AGENTS.md\` for the same topic
- If a context file already captures the information, trim the facts file to a brief reference (e.g., "See USER.md for details about X")
- Context files are more authoritative for their respective categories; facts should supplement, not duplicate

## Deletion

- If a fact file is entirely obsolete (all entries stale, no useful content), delete it.
- Do NOT delete files that contain any useful or current information.

## Idempotency

The goal is to leave the store materially smaller and more consolidated than you found it. Treat absence of exact duplicates as insufficient grounds for skipping — semantic overlap and per-incident granularity are also valid triggers for action.

### Consolidate narrow per-incident files into broader topic files

When you see multiple narrow files about the same subject (per-date, per-incident, per-PR, per-bug), merge them into a single broader topic file. Example: \`<project>-bug-<YYYY-MM-DD>.md\`, \`<project>-process.md\`, and \`<project>-rollout-notes.md\` should all be folded into \`<project>.md\`. Keep the most durable, generally-useful information; discard the incident-specific noise. Delete the merged-from files after consolidation.

### Aggressively prune resolved incidents

Files whose content carries headers like "Status: Completed", "Status: Merged", "Status: Resolved", or "Status: Fixed" describe one-time events — that content belongs in episodic memory, not facts. Delete these files outright. If a fact file mixes durable content with a resolved-incident section, strip the resolved section and keep the rest.

If no changes are needed, exit with no changes.`;

const PREFERENCES_MAINTENANCE_PROMPT = `You are a memory maintenance agent performing preferences memory cleanup.

## Directory

\`$WORKSPACE/memories/preferences/\`

## Pre-check

If the directory is empty or contains no \`.md\` files, stop immediately — nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with \`.md\` extension.

## Evaluation Criteria

Read all preference files and evaluate each for these issues:

### Redundancy

Same preference stated multiple times across or within files:
- Keep the most complete and well-organized version.
- Remove the duplicate entries.

### Overlap

Related preferences split across multiple files:
- When files cover overlapping topics (e.g., coding style preferences split across "python-style.md" and "code-formatting.md"), merge them into a single consolidated file.
- Choose the best filename from the originals, or create a more descriptive one.
- Remove the original files after merging.

#### Cluster Consolidation

Beyond pairwise overlap, look for clusters of many small files that fragment a single broad preference topic into per-occasion, per-feedback, or per-date entries — this is the main driver of preferences directory bloat:

- List all files in \`$WORKSPACE/memories/preferences/\` and group them by shared prefix or core topic (style, workflow, communication, tooling, project, system, or domain).
- When 3 or more files share the same prefix or core topic, treat them as a cluster and consolidate aggressively:
  - Merge all of the cluster's content into a single broad-topic file named \`<topic-area>-style.md\`, \`<topic-area>-workflow.md\`, \`<domain>.md\`, or \`<project>.md\` — the same broad-topic convention used at extraction time.
  - If a broad-topic file already exists in the cluster, merge the others into it. Otherwise, pick a broad name and create the consolidated file.
  - Delete the original narrow files after merging.
- Generic examples of clusters to consolidate (patterns, not literal names): files matching \`<topic-area>-feedback-<YYYY-MM-DD>.md\`, \`<project>-preference-<issue-id>.md\`, \`<topic-area>-session-<date>.md\`, \`<one-off>-incident-<date>.md\` — all of these should fold into the broad \`<topic-area>-style.md\`, \`<topic-area>-workflow.md\`, or \`<topic>.md\` file.
- Preserve substantive content during the merge; deduplicate restated preferences and keep only one clear statement per preference.

### Misclassification

Content that belongs in facts memory, not preferences:
- Read each file and ask: "Does this describe a subjective choice (preference) or reference information (fact)?"
- If the file describes how something works, technical specifications, financial structures, system configurations, or procedural workflows → it is misclassified
- For misclassified files: delete the preferences file. The facts extraction processor will pick the content up on the next relevant conversation.

### Size Enforcement

Flag any file exceeding 30 lines for consolidation or pruning:
- If a file exceeds 30 lines, it likely contains reference information that belongs in facts, or multiple preferences that could be expressed more concisely
- Prune redundant statements within the file
- If the excess is factual content, treat as misclassification (see above)

### Cross-Store Overlap with Facts

Check \`$WORKSPACE/memories/facts/\` for files covering the same topic:
- If a facts file already covers the topic, the preferences file should only contain genuinely subjective aspects (how the user wants things done) — not the factual details already captured in facts
- If a preferences file contains only factual content that a facts file already covers, delete the preferences file — it serves no purpose

## Deletion

- If a preference file is entirely superseded (its preferences are all present in a newer, more complete file), delete it.
- If a preference file describes preferences the user no longer holds (e.g., contradicted by a newer entry), remove only the outdated entries — or delete the file if it becomes empty.
- Do NOT delete files that contain any current, unique preferences.

## Idempotency

Treat absence of exact duplicates as insufficient grounds for skipping — semantic overlap and split topics are also triggers for action. Actively re-apply the Evaluation Criteria and Deletion sections above: merge overlapping files even when they aren't word-for-word duplicates, and remove reversed or superseded preferences even when they aren't explicitly contradicted in a newer file.

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
   - Determine which store is more authoritative per the authority hierarchy (Skills > Facts > Context)
   - If this store is LESS authoritative: update or remove the contradicting entry in this store to match the more authoritative source
   - If this store is MORE authoritative: leave this store's entry unchanged (the other store's maintenance tick will handle it)
4. When information in this store duplicates detail from a more authoritative store: trim this store's entry to a brief pointer (e.g., "See memories/facts/X.md for details")`;

const STORE_LABELS: Record<MemoryStore, string> = {
  episodic: "Episodic Files",
  facts: "Facts Files",
  preferences: "Preferences Files",
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
- **USER.md** must stay under ~120 lines. When it exceeds the limit: summarize verbose sections, remove stale sections, or omit details that belong in facts/preferences memory.
- **AGENTS.md** must stay under ~400 lines. When it exceeds the limit: remove entries about resolved bugs or completed work, and consolidate duplicated entries across sections.

## Constraints

- **Cleanup-only**: Do NOT add new content — only clean and consolidate what is already there. Adding new content from conversations is the job of the per-session context update, not this maintenance pass.
- **Read-first**: Always read a file before modifying it.
- **Preserve structure**: Keep existing formatting and organization.
- **Conservative**: Only remove content with clear evidence of staleness.
- **SOUL.md**: Be especially conservative — personality traits and tone guidelines should only be removed when the user has explicitly contradicted them or when they duplicate each other.

## Idempotency

Treat absence of obvious problems as insufficient grounds for skipping — actively re-apply the Evaluation Criteria and Size Limits above. Stale instructions added for resolved incidents and entries that belong in facts/preferences memory accumulate quietly between runs and won't always surface in a quick scan.

If no changes are needed, exit with no changes.

## Scope

You can read files anywhere in the workspace (needed to validate claims against actual project state). Only modify \`$WORKSPACE/SOUL.md\`, \`$WORKSPACE/USER.md\`, and \`$WORKSPACE/AGENTS.md\`. Do not create, delete, or modify any other files.`;

/** Names-only listing of the other stores so the maintenance agent can reconcile across them. */
export const buildCrossStoreManifest = async (
  workspaceRoot: string,
  current: MemoryStore,
): Promise<string | null> => {
  const sections: string[] = [];

  for (const store of MEMORY_STORES) {
    if (store === current) continue;

    let files: string[];
    try {
      files = (await readdir(storeDir(workspaceRoot, store)))
        .filter((name) => name.endsWith(".md"))
        .sort();
    } catch {
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

const basePrompt = (store: MemoryStore, settings: MaintenanceThresholds): string => {
  if (store === "episodic") return episodicMaintenancePrompt(settings);
  if (store === "facts") return FACTS_MAINTENANCE_PROMPT;
  return PREFERENCES_MAINTENANCE_PROMPT;
};

export const maintenanceSystemPrompt = async (
  store: MemoryStore,
  { workspaceRoot, settings, now }: Pick<MaintenanceDeps, "workspaceRoot" | "settings" | "now">,
): Promise<string> => {
  const parts = [basePrompt(store, settings)];

  // Day-of-week dispatch: weekdays keep the index consistent cheaply,
  // Sunday rebuilds it from scratch with fresh descriptions.
  if (store !== "episodic") {
    const isSunday = (now?.() ?? new Date()).getDay() === 0;
    parts.push(isSunday ? heavyIndexRebuildSection(store) : INDEX_LIGHT_MAINTENANCE_SECTION);
  }

  parts.push(STORE_PURPOSE_SECTION);

  const manifest = await buildCrossStoreManifest(workspaceRoot, store);
  if (manifest != null) parts.push(manifest);

  parts.push(CONTRADICTION_DETECTION_SECTION, scopeSection(store));

  return parts.join("\n\n").replaceAll("$WORKSPACE", workspaceRoot);
};

/** Names-only listing of the memory stores so the context tick can reconcile against more authoritative facts/preferences. */
export const buildStoreManifestForContext = async (
  workspaceRoot: string,
): Promise<string | null> => {
  const sections: string[] = [];

  for (const store of MEMORY_STORES) {
    let files: string[];
    try {
      files = (await readdir(storeDir(workspaceRoot, store)))
        .filter((name) => name.endsWith(".md"))
        .sort();
    } catch {
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

  if (sections.length === 0) return null;

  return [
    "## Memory Store Visibility",
    "",
    "Files in the memory stores (names and paths only, not content):",
    "",
    sections.join("\n\n"),
    "",
    'When a context-file section duplicates detail already captured in a more authoritative memory facts file, trim it to a brief pointer (e.g., "See memories/facts/X.md for details") rather than inlining the content.',
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

  await deps.side.run({
    tools: MEMORY_FILE_TOOLS,
    system: await contextMaintenanceSystemPrompt(deps.workspaceRoot),
    prompt: "Perform the context file cleanup pass now, following your instructions.",
    tier: "processor",
  });

  await deps.commitChanges?.("chore(memory): scheduled context file maintenance");

  deps.log.info("context maintenance tick completed");
};

/** Daily maintenance pass over one memory store: consolidate, prune, keep indexes in sync. */
export const runMaintenanceTick = async (
  store: MemoryStore,
  deps: MaintenanceDeps,
): Promise<void> => {
  deps.log.info({ store }, "memory maintenance tick started");

  await deps.side.run({
    tools: MEMORY_FILE_TOOLS,
    system: await maintenanceSystemPrompt(store, deps),
    prompt: "Perform the maintenance pass now, following your instructions.",
    tier: "processor",
  });

  await sweepEmptyMarkdown(storeDir(deps.workspaceRoot, store), deps.log);

  await deps.commitChanges?.(`chore(memory): scheduled ${store} maintenance`);

  deps.log.info({ store }, "memory maintenance tick completed");
};

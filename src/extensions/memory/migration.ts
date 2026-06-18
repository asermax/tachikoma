import { readdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";

import { FILE_EDIT_TOOLS } from "../../agent/file-tools.ts";
import type { Logger } from "../../log.ts";
import type { Runner } from "./extraction.ts";
import { isBlankMarkdown, listMarkdown, memoriesRoot, storeDir } from "./layout.ts";
import {
  CONTEXT_DEDUP_SECTION,
  INDEX_UPDATE_SECTION,
  STORE_PURPOSE_SECTION,
  scopeSection,
} from "./prompts.ts";

/**
 * One-time, self-detecting, idempotent fold of the legacy `facts/` and `preferences/`
 * memory stores into the unified `topics/` store. Run as a bootstrap hook before
 * `init-memory-layout` so the first session's memory index already reflects the
 * migrated topics (design S7 / KD4). See KD5–KD6 for the merge + detection rationale.
 */

export interface MigrationDeps {
  side: Runner;
  workspaceRoot: string;
  log: Logger;
  /** Commit workspace changes (the fold and the removal each commit, so git is the backup). */
  commitChanges: (message: string) => Promise<void>;
}

// The two retired stores. `MemoryStore` no longer includes these (Batch 1 narrowed it to
// `episodic | topics`), so they are resolved by hand off `memoriesRoot` rather than via `storeDir`.
const LEGACY_STORES = ["facts", "preferences"] as const;

const legacyStoreDir = (workspaceRoot: string, store: string): string =>
  join(memoriesRoot(workspaceRoot), store);

interface StoreScan {
  /** All `.md` filenames found (for the auditable summary). */
  files: string[];
  /** How many held real content (drove detection). */
  nonEmpty: number;
}

// A missing legacy dir (ENOENT) reads as an empty store — fresh installs and already-migrated
// workspaces never have them.
const scanLegacyStore = async (dir: string): Promise<StoreScan> => {
  let names: string[];

  try {
    names = await readdir(dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { files: [], nonEmpty: 0 };
    throw error;
  }

  const markdown = names.filter((name) => name.endsWith(".md")).sort();
  let nonEmpty = 0;

  for (const name of markdown) {
    const path = join(dir, name);

    try {
      if (!(await isBlankMarkdown(path, await stat(path)))) nonEmpty += 1;
    } catch {
      // Unreadable file: a soft failure — leave it in place so it is retried next run,
      // and do not count it as content that would gate a fold on unreadable bytes alone.
    }
  }

  return { files: markdown, nonEmpty };
};

// Remove a legacy store outright (files and all). The fold is committed before this runs, so git
// is the backup. A blank-only sweep would leave content-bearing files behind, and since detection
// keys on legacy-content presence the migration would then re-run on every startup.
const removeLegacyStore = async (dir: string, log: Logger): Promise<void> => {
  await rm(dir, { recursive: true, force: true }).catch((error) => {
    log.debug({ dir, err: error }, "could not remove legacy memory store dir");
  });
};

const foldSystemPrompt = (workspaceRoot: string): string =>
  [
    `You are migrating two legacy memory stores into the unified topic store.

## Legacy stores to fold

- \`$WORKSPACE/memories/facts/\` — stable, objective reference facts (one subject per file)
- \`$WORKSPACE/memories/preferences/\` — subjective choices and preferences (one subject per file)

## Target store

- \`$WORKSPACE/memories/topics/\` — everything known about a subject, both reference facts and subjective preferences together, one topic per file.

## Steps

1. **Read everything first.** Read every \`.md\` file in BOTH legacy stores, and read every existing file in \`memories/topics/\` (if any) so you fold into what is already there.

2. **Fold durable content into broad topic files.** For each subject:
   - Files about the SAME subject across the two legacy stores (and across legacy + existing topics) MERGE into a single topic file — the objective reference detail and the subjective preferences for that subject live together in that one file, in clear unified prose.
   - Files about DISTINCT subjects stay as distinct topic files.
   - Decide the grouping by subject, not by which legacy store a file came from — a facts file and a preferences file about the same project, tool, or person belong in ONE topic file.

3. **Merge and deduplicate so a re-run never duplicates.** This migration is re-runnable: if \`topics/\` already holds a file for a subject (e.g. after an interrupted earlier pass), UPDATE that file rather than creating or appending a duplicate. Merge new detail into existing sections; never write the same fact twice.

4. **Use broad-topic naming.** Choose broad, future-mergeable filenames — \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, \`<domain>.md\`, \`work-info.md\`, \`tech-stack.md\`, \`communication-style.md\` — never a name scoped to one incident, bug, or date.

5. **Skip empty or unreadable legacy files.** A 0-byte, whitespace-only, or garbled legacy file holds no durable content — do NOT create an empty topic file for it. Leave such files untouched; the host sweeps them afterward.

6. **Do not touch the legacy stores.** You can READ \`memories/facts/\` and \`memories/preferences/\` but must only CREATE or MODIFY files under \`memories/topics/\`. The host empties and removes the legacy stores after your fold is committed — your only output is the topic files and their index.

7. **Prune while folding.** Drop content that is stale, redundant, or better covered by an existing topic; keep durable reference facts and preferences that remain accurate.`,
    STORE_PURPOSE_SECTION,
    CONTEXT_DEDUP_SECTION,
    INDEX_UPDATE_SECTION,
    scopeSection("topics"),
  ]
    .join("\n\n")
    .replaceAll("$WORKSPACE", workspaceRoot);

/**
 * Fold legacy `facts/` + `preferences/` into `topics/`, with no data loss. Detection is
 * state-based (no persisted marker): it runs iff a legacy store holds any non-empty `.md`,
 * and is a no-op once both are gone. Two commits bracket the removal — fold → commit →
 * remove → commit — so folded topics are durable in git before any legacy store is deleted.
 * A hard agent-run error aborts WITHOUT removing anything, so the old stores still hold content
 * and the whole pass retries cleanly on the next startup (KD6).
 */
export const migrateMemoryStores = async (deps: MigrationDeps): Promise<void> => {
  const { side, workspaceRoot, log, commitChanges } = deps;

  const scans = {} as Record<(typeof LEGACY_STORES)[number], StoreScan>;
  for (const store of LEGACY_STORES) {
    scans[store] = await scanLegacyStore(legacyStoreDir(workspaceRoot, store));
  }
  const legacyNonEmpty = scans.facts.nonEmpty + scans.preferences.nonEmpty;

  if (legacyNonEmpty === 0) {
    // Fresh install or already migrated (or interrupted-but-already-removed): no content to fold.
    log.debug(
      { facts: scans.facts.nonEmpty, preferences: scans.preferences.nonEmpty },
      "no legacy facts/preferences content — memory-store migration is a no-op",
    );
    return;
  }

  log.info(
    { facts: scans.facts.nonEmpty, preferences: scans.preferences.nonEmpty },
    "legacy memory stores detected — folding facts + preferences into topics",
  );

  const start = Date.now();

  try {
    await side.run({
      tools: FILE_EDIT_TOOLS,
      system: foldSystemPrompt(workspaceRoot),
      prompt:
        "Fold every legacy facts/ and preferences/ file into the topics store now, following your instructions.",
      tier: "processor",
    });
  } catch (error) {
    // Hard failure: abort WITHOUT removing or the removal-commit. The legacy stores still hold
    // content, so this whole pass re-runs on the next startup (the fold's dedup re-merges any
    // partial topic writes). No partial corruption is committed.
    log.warn(
      { err: error instanceof Error ? error.message : String(error) },
      "memory-store migration fold failed — legacy stores left untouched, will retry next startup",
    );
    return;
  }

  // Fold-before-remove (R7): commit the folded topics so they are durable in git BEFORE any
  // legacy store is touched. Then remove the legacy stores outright and commit that cutover.
  await commitChanges("chore(memory): migrate facts+preferences into topics");

  for (const store of LEGACY_STORES) {
    await removeLegacyStore(legacyStoreDir(workspaceRoot, store), log);
  }

  await commitChanges("chore(memory): remove legacy memory stores");

  const topicsProduced = (await listMarkdown(storeDir(workspaceRoot, "topics"))).length;

  log.info(
    {
      facts: scans.facts.nonEmpty,
      preferences: scans.preferences.nonEmpty,
      topicsProduced,
      durationMs: Date.now() - start,
    },
    "memory-store migration completed — legacy facts/preferences folded into topics",
  );
};

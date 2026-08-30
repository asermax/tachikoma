import { readdir, readFile, stat, unlink } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../log.ts";

/**
 * Generic helpers for the markdown wiki stores (memory's `memories/` trees, skill-evolution's
 * pattern pages): index-aware listing, blank-file detection, and the empty-file sweep. Promoted
 * from memory's `layout.ts` so every store keeps ONE implementation of the wiki conventions
 * (DES-002 — shared helpers live in a neutral module, not inside an extension).

 * The store-agnostic rules: `MEMORY.md` is the index (never swept as content, never listed as a
 * page); an agent that cannot delete files empties them instead, and the host removes the leftovers.
 */

/** The index filename every markdown store uses for its one-line-per-page directory. */
export const MEMORY_INDEX_FILENAME = "MEMORY.md";

export const fileExists = async (path: string): Promise<boolean> => {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
};

/**
 * List the markdown files in a store dir, excluding the `MEMORY.md` index and
 * tolerating a missing dir (ENOENT reads as empty). The single source for
 * "which .md files live in this store", shared by layout seeding, store
 * inventories, and migrations. Callers with more exclusions (e.g. a ledger
 * file) filter further on the returned names.
 */
export const listMarkdown = async (dir: string): Promise<string[]> => {
  let names: string[];
  try {
    names = await readdir(dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }

  return names.filter((name) => name.endsWith(".md") && name !== MEMORY_INDEX_FILENAME).sort();
};

/**
 * Whether a `.md` file counts as blank (no durable content). The single source
 * of truth for "what counts as content" — shared by `sweepEmptyMarkdown` (what
 * gets removed) and any blank-file detection gate, so the sweep and the gate
 * can never drift apart.
 */
export const isBlankMarkdown = async (path: string, info: { size: number }): Promise<boolean> =>
  info.size === 0 || (info.size <= 64 && (await readFile(path, "utf8")).trim() === "");

/**
 * The store-maintaining agents have no delete tool — they empty files
 * instead, and the host removes those leftovers here after each run.
 */
export const sweepEmptyMarkdown = async (dir: string, log: Logger): Promise<void> => {
  let names: string[];

  try {
    names = await readdir(dir);
  } catch {
    return;
  }

  for (const name of names) {
    if (!name.endsWith(".md")) continue;

    const path = join(dir, name);

    try {
      const info = await stat(path);

      if (!(await isBlankMarkdown(path, info))) continue;

      await unlink(path);
      log.info({ path }, "removed emptied markdown file");
    } catch (error) {
      log.warn({ path, err: error }, "failed to sweep markdown file");
    }
  }
};

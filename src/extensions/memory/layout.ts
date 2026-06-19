import { mkdir, readdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";

// Store-set seam — the single source of truth every module iterates (see design S1). Adding a
// store is membership, not a rewrite: dir creation derives from MEMORY_STORES (ensureMemoryLayout),
// index seeding + context injection from INDEXED_STORES, maintenance + cross-store manifests from
// MEMORY_STORES, and extraction from EXTRACTION_STORES. The one asymmetry — `learnings` is in the
// top two sets but absent from EXTRACTION_STORES — is what folds learnings into the topics fork and
// prevents a duplicate (R4).
export const MEMORY_STORES = ["episodic", "topics", "learnings"] as const;
export type MemoryStore = (typeof MEMORY_STORES)[number];

export const INDEXED_STORES = ["topics", "learnings"] as const satisfies readonly MemoryStore[];

// Stores that get their own extraction fork. `learnings` is folded INTO the topics fork (never its
// own), so it is excluded here — see extractBranches and the shared topics+learnings instruction.
export const EXTRACTION_STORES = ["episodic", "topics"] as const satisfies readonly MemoryStore[];
export type ExtractionStore = (typeof EXTRACTION_STORES)[number];

// Which stores each extraction fork WRITES to — drives the post-run sweep. Learnings is folded into
// the topics fork, so that fork writes both `topics/` and `learnings/` and must sweep both. Keyed on
// ExtractionStore so the sweep is exhaustive at compile time (every fork maps to its target dirs).
export const FORK_WRITE_STORES: Readonly<Record<ExtractionStore, readonly MemoryStore[]>> = {
  episodic: ["episodic"],
  topics: ["topics", "learnings"],
};

export const MEMORY_INDEX_FILENAME = "MEMORY.md";

export const memoriesRoot = (workspaceRoot: string): string => join(workspaceRoot, "memories");

export const storeDir = (workspaceRoot: string, store: MemoryStore): string =>
  join(memoriesRoot(workspaceRoot), store);

export const transcriptsDir = (workspaceRoot: string): string =>
  join(memoriesRoot(workspaceRoot), "transcripts");

export const fileExists = async (path: string): Promise<boolean> => {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
};

const titleFromFilename = (filename: string): string =>
  filename
    .replace(/\.md$/, "")
    .split(/[-_]/)
    .filter((part) => part !== "")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

/**
 * List the markdown files in a store dir, excluding the `MEMORY.md` index and
 * tolerating a missing dir (ENOENT reads as empty). The single source for
 * "which .md files live in this store", shared by layout seeding, the store
 * manifests, and the legacy migration's topic count.
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
 * Create the memory directory layout and seed MEMORY.md indexes (idempotent).
 * Pre-existing memory files get placeholder index entries; the maintenance
 * ticks regenerate proper descriptions later.
 */
export const ensureMemoryLayout = async (workspaceRoot: string, log: Logger): Promise<void> => {
  const dirs = [
    ...MEMORY_STORES.map((store) => storeDir(workspaceRoot, store)),
    transcriptsDir(workspaceRoot),
  ];

  for (const dir of dirs) await mkdir(dir, { recursive: true });

  for (const store of INDEXED_STORES) {
    const indexPath = join(storeDir(workspaceRoot, store), MEMORY_INDEX_FILENAME);

    if (await fileExists(indexPath)) continue;

    const entries = (await listMarkdown(storeDir(workspaceRoot, store))).map(
      (name) => `[${titleFromFilename(name)}](./${name}): Description pending update`,
    );

    const content = `${["# Memory Index", "", ...entries].join("\n").trimEnd()}\n`;
    await writeFile(indexPath, content, "utf8");

    log.info({ store, entries: entries.length }, "memory index created");
  }
};

/**
 * Whether a `.md` file counts as blank (no durable content). The single source
 * of truth for "what counts as content" — shared by `sweepEmptyMarkdown` (what
 * gets removed) and the legacy-store migration's detection gate, so the sweep
 * and the detection gate can never drift apart.
 */
export const isBlankMarkdown = async (path: string, info: { size: number }): Promise<boolean> =>
  info.size === 0 || (info.size <= 64 && (await readFile(path, "utf8")).trim() === "");

/**
 * The extraction/maintenance agents have no delete tool — they empty files
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
      log.info({ path }, "removed emptied memory file");
    } catch (error) {
      log.warn({ path, err: error }, "failed to sweep memory file");
    }
  }
};

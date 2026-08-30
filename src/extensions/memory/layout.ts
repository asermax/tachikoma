import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";
import { fileExists, listMarkdown, MEMORY_INDEX_FILENAME } from "../../util/markdown-store.ts";

// The store sets every module iterates. `learnings` is absent from EXTRACTION_STORES on purpose —
// the topics fork writes it too, so a separate learnings extraction would duplicate.
export const MEMORY_STORES = ["episodic", "topics", "learnings"] as const;
export type MemoryStore = (typeof MEMORY_STORES)[number];

export const INDEXED_STORES = ["topics", "learnings"] as const satisfies readonly MemoryStore[];

/** Whether a store owns a seeded MEMORY.md index (topics + learnings do; episodic does not). */
const INDEXED_STORE_SET: ReadonlySet<MemoryStore> = new Set<MemoryStore>(INDEXED_STORES);
export const isIndexedStore = (store: MemoryStore): boolean => INDEXED_STORE_SET.has(store);

// Stores that get their own extraction fork. `learnings` is folded into the topics fork, never its own.
export const EXTRACTION_STORES = ["episodic", "topics"] as const satisfies readonly MemoryStore[];
export type ExtractionStore = (typeof EXTRACTION_STORES)[number];

// The stores each extraction fork writes — drives the post-run sweep. Keyed on ExtractionStore so
// every fork's write surface (and thus sweep) is exhaustive at compile time.
export const FORK_WRITE_STORES: Readonly<Record<ExtractionStore, readonly MemoryStore[]>> = {
  episodic: ["episodic"],
  topics: ["topics", "learnings"],
};

export const memoriesRoot = (workspaceRoot: string): string => join(workspaceRoot, "memories");

export const storeDir = (workspaceRoot: string, store: MemoryStore): string =>
  join(memoriesRoot(workspaceRoot), store);

export const transcriptsDir = (workspaceRoot: string): string =>
  join(memoriesRoot(workspaceRoot), "transcripts");

// The generic markdown-store helpers live in the neutral util (DES-002) so other stores share one
// implementation; re-exported here so every existing memory importer keeps its import path.
export {
  fileExists,
  isBlankMarkdown,
  listMarkdown,
  MEMORY_INDEX_FILENAME,
  sweepEmptyMarkdown,
} from "../../util/markdown-store.ts";

const titleFromFilename = (filename: string): string =>
  filename
    .replace(/\.md$/, "")
    .split(/[-_]/)
    .filter((part) => part !== "")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

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

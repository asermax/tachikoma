import { readFile } from "node:fs/promises";
import { join } from "node:path";

import type { ContextProvider } from "../api.ts";
import {
  fileExists,
  INDEXED_STORES,
  MEMORY_INDEX_FILENAME,
  type MemoryStore,
  memoriesRoot,
  storeDir,
} from "./layout.ts";

const INDEX_ENTRY_RE = /^\[([^\]]+)\]\(\.\/([^)]+\.md)\):\s*(.+)$/gm;

const STORE_DESCRIPTIONS: Partial<Record<MemoryStore, string>> = {
  facts:
    "Stable reference information: personal details, key people, technical decisions.\nBrowse the entries below. When a file seems relevant to the current conversation,\nread it with the read tool to get the full content.",
  preferences:
    "Subjective choices about how things should be done.\nBrowse the entries below. When a file seems relevant to the current conversation,\nread it with the read tool to get the full content.",
};

const LAYOUT_SECTION = `## Memory

The workspace keeps long-term memory as markdown files under \`memories/\`:

- \`memories/episodic/\` — date-stamped conversation summaries (\`YYYY-MM-DD.md\`, plus weekly \`YYYY-WNN.md\` and monthly \`YYYY-MM.md\` rollups)
- \`memories/facts/\` — stable reference information, one topic per file, indexed in \`MEMORY.md\`
- \`memories/preferences/\` — the user's expressed preferences, one topic per file, indexed in \`MEMORY.md\`

None of these files are loaded automatically. When the conversation touches a topic that might be covered there, grep or read the relevant memory files on demand.`;

const capitalize = (value: string): string => value.charAt(0).toUpperCase() + value.slice(1);

/**
 * Format a MEMORY.md file's raw content into an injectable section. Entries
 * must match "[Name](./path.md): description"; malformed lines are skipped.
 * Returns null when the file has no usable entries.
 */
export const formatMemoryIndex = (store: MemoryStore, rawContent: string): string | null => {
  const entries = [...rawContent.matchAll(INDEX_ENTRY_RE)].map((match) => match[0]);

  if (entries.length === 0) return null;

  const description =
    STORE_DESCRIPTIONS[store] ??
    "Browse the entries below. When a file seems relevant, read it with the read tool.";

  return [
    `## ${capitalize(store)} Index`,
    "",
    description,
    "",
    ...entries.map((entry) => `- ${entry}`),
  ].join("\n");
};

/**
 * Static memory index injection: the workspace layout plus the parsed facts and
 * preferences MEMORY.md indexes, so the agent knows what exists and reads files
 * on demand instead of getting everything inlined.
 */
export const createMemoryIndexProvider = (workspaceRoot: string): ContextProvider => ({
  name: "memory-index",

  async provide() {
    if (!(await fileExists(memoriesRoot(workspaceRoot)))) return null;

    const sections = [LAYOUT_SECTION];

    for (const store of INDEXED_STORES) {
      let raw: string;

      try {
        raw = await readFile(join(storeDir(workspaceRoot, store), MEMORY_INDEX_FILENAME), "utf8");
      } catch {
        continue;
      }

      const formatted = formatMemoryIndex(store, raw);
      if (formatted != null) sections.push(formatted);
    }

    return { tag: "memories", content: sections.join("\n\n") };
  },
});

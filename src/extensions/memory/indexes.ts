import { readFile } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";
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
  topics:
    "Everything worth remembering about a subject: reference facts, preferences, insights,\ndecisions, and conclusions together, one topic per file. Browse the entries below. When a file seems\nrelevant to the current conversation, read it with the read tool to get the full content.",
  learnings:
    "Experience notes: recurring friction, hard constraints, and hard-won lessons — what\nbites and what worked, one theme per file (drafts are tentative, confirmed entries are\nrecurring). Browse the entries below. When a file seems relevant to the current\nconversation, read it with the read tool to get the full content.",
};

const LAYOUT_SECTION = `## Memory

The workspace keeps long-term memory as markdown files under \`memories/\`:

- \`memories/episodic/\` — date-stamped conversation summaries (\`YYYY-MM-DD.md\`, plus weekly \`YYYY-WNN.md\` and monthly \`YYYY-MM.md\` rollups)
- \`memories/topics/\` — everything worth remembering about a subject (reference facts, preferences, insights, decisions, and conclusions together), one topic per file, indexed in \`MEMORY.md\`
- \`memories/learnings/\` — recurring friction, hard constraints, and hard-won lessons (experience, not knowledge), one theme per file, indexed in \`MEMORY.md\`
- \`memories/transcripts/\` — archived raw conversation transcripts

None of these files are loaded automatically. When the conversation touches a topic that might be covered there, grep or read the relevant memory files on demand. You do not write to \`memories/\` directly — an automated post-processing pass maintains these files (creating, updating, and consolidating them) after each conversation ends.`;

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
 * Static memory context: the workspace layout (and read-only/post-processing note) plus the
 * parsed topics MEMORY.md index, so the agent knows what exists and reads files on demand
 * instead of getting everything inlined. Returns "" when no memory store exists yet (no
 * section injected).
 */
export const buildMemoryContext = async (workspaceRoot: string, log: Logger): Promise<string> => {
  if (!(await fileExists(memoriesRoot(workspaceRoot)))) return "";

  const sections = [LAYOUT_SECTION];

  for (const store of INDEXED_STORES) {
    let raw: string;

    try {
      raw = await readFile(join(storeDir(workspaceRoot, store), MEMORY_INDEX_FILENAME), "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        log.debug({ err: error, store }, "memory index unreadable — skipping store");
      }

      continue;
    }

    const formatted = formatMemoryIndex(store, raw);
    if (formatted != null) sections.push(formatted);
  }

  return sections.join("\n\n");
};

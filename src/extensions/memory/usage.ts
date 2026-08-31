import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * The static memory-layout section of the `memories` context. Exported as the extension's
 * usage constant (DES-002 convention) so the static-content sweep and size budget enumerate
 * it mechanically; `buildMemoryContext` composes it with the dynamic store indexes. Scoped
 * main + background alongside the provider that injects it.
 */
export const MEMORY_LAYOUT_USAGE = `## Memory

Long-term memory lives as markdown under \`memories/\`:
- \`memories/episodic/\` — date-stamped conversation summaries
- \`memories/topics/\` — knowledge about a subject, one topic per file
- \`memories/learnings/\` — recurring friction and hard-won lessons, one theme per file
- \`memories/transcripts/\` — archived raw conversation transcripts

None of it is loaded automatically — when the conversation touches something these files might cover, grep or read them on demand. You do not write to \`memories/\`: an automated pass maintains the store after each conversation.

${referencePointer(import.meta.dirname, "memory")}`;

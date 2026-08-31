import { join } from "node:path";

/**
 * The two-tier documentation convention for agent-facing guidance (issue-445): each usage section
 * stays lean inline (what the feature is, when to reach for it, critical rules) and points at a
 * reference file the agent reads only when it needs the detail — the same progressive-disclosure
 * pattern skills use. Reference files live in a `references/` directory next to the owning module
 * (`src/extensions/<name>/references/<topic>.md`, `src/agent/references/<topic>.md`) and ship with
 * the package via `scripts/copy-assets.mjs` (tsc only emits JS).
 *
 * This helper is the single source of the pointer line so every section emits the same shape —
 * including the absolute path the drift test (tests/agent/prompt-references.test.ts) greps for.
 *
 * @example referencePointer(import.meta.dirname, "branches")
 *   // "Details: /…/src/extensions/boundary/references/branches.md (read on demand)"
 */
export const referencePointer = (moduleDir: string, topic: string): string =>
  `Details: ${join(moduleDir, "references", `${topic}.md`)} (read on demand)`;

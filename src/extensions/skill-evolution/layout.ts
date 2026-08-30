import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../../log.ts";
import { fileExists, MEMORY_INDEX_FILENAME } from "../../util/markdown-store.ts";
import { formatImpactLog, IMPACT_LOG_FILENAME } from "./store.ts";

/**
 * The skill-evolution store layout under `memories/skill-evolution/` (S1): a `MEMORY.md` index
 * plus the host-written impact ledger. Owns its path helpers — never memory's (DES-002).
 * Seeding follows the `ensureMemoryLayout` idiom: mkdir recursive, then seed each file only
 * when absent, so user edits survive every startup.
 */

export const skillEvolutionDir = (workspaceRoot: string): string =>
  join(workspaceRoot, "memories", "skill-evolution");

export const impactLogPath = (workspaceRoot: string): string =>
  join(skillEvolutionDir(workspaceRoot), IMPACT_LOG_FILENAME);

// The index header documents the one-line-per-pattern convention (R4) in the file itself, so the
// agents that maintain the store see the expected entry form.
const INDEX_SEED = `${[
  "# Skill Evolution Index",
  "",
  "One line per pattern page, in the form:",
  "`- [Title](./pattern-slug.md): PROBLEM — ROOT CAUSE — FIX`",
].join("\n")}\n`;

export const ensureSkillEvolutionLayout = async (
  workspaceRoot: string,
  log: Logger,
): Promise<void> => {
  const dir = skillEvolutionDir(workspaceRoot);
  await mkdir(dir, { recursive: true });

  const indexPath = join(dir, MEMORY_INDEX_FILENAME);
  if (!(await fileExists(indexPath))) {
    await writeFile(indexPath, INDEX_SEED, "utf8");
    log.info("skill-evolution index created");
  }

  const ledgerPath = join(dir, IMPACT_LOG_FILENAME);
  if (!(await fileExists(ledgerPath))) {
    // The seed is the canonical empty ledger — header-only, straight from the store formatter,
    // so the seeded file and every write-back share one shape.
    await writeFile(ledgerPath, formatImpactLog([]), "utf8");
    log.info("skill-evolution impact log created");
  }
};

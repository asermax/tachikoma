import { readdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import type { Ask } from "./ask.ts";

// Frontmatter keys only the legacy skill registry consumed; pi's Agent Skills
// loader only knows name/description/disable-model-invocation and ignores the rest.
const LEGACY_ONLY_KEYS = ["depends_on", "version"];

export interface FrontmatterScan {
  keys: string[];
  content: string;
}

/**
 * Detect legacy-only frontmatter keys in a SKILL.md and produce a copy with
 * those keys (and their indented/list continuation lines) removed.
 * Returns null when the file has no frontmatter or no legacy-only keys.
 */
export const stripLegacyFrontmatter = (source: string): FrontmatterScan | null => {
  const lines = source.split("\n");

  if (lines[0]?.trim() !== "---") return null;

  const close = lines.findIndex((line, index) => index > 0 && line.trim() === "---");

  if (close === -1) return null;

  const kept: string[] = [];
  const found = new Set<string>();
  let skipping = false;

  for (const line of lines.slice(1, close)) {
    const key = /^([\w-]+)\s*:/.exec(line)?.[1];

    if (key != null) {
      skipping = LEGACY_ONLY_KEYS.includes(key);

      if (skipping) {
        found.add(key);
        continue;
      }
    }

    if (skipping) continue;

    kept.push(line);
  }

  if (found.size === 0) return null;

  return {
    keys: [...found],
    content: [lines[0], ...kept, ...lines.slice(close)].join("\n"),
  };
};

/**
 * Scan workspace skills for legacy-only frontmatter keys and offer to strip
 * them. Declining (or a non-interactive startup) keeps the files as-is with a
 * warning — pi simply ignores the keys.
 */
export const adaptSkillsFrontmatter = async (
  workspace: Workspace,
  log: Logger,
  ask: Ask,
): Promise<void> => {
  const skillsDir = workspace.resolve("skills");
  const entries = await readdir(skillsDir, { withFileTypes: true }).catch(() => []);
  const affected: { path: string; scan: FrontmatterScan }[] = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;

    const skillFile = join(skillsDir, entry.name, "SKILL.md");

    let source: string;
    try {
      source = await readFile(skillFile, "utf8");
    } catch {
      continue;
    }

    const scan = stripLegacyFrontmatter(source);

    if (scan != null) affected.push({ path: skillFile, scan });
  }

  if (affected.length === 0) return;

  const keys = [...new Set(affected.flatMap(({ scan }) => scan.keys))].join(", ");

  if (
    !(await ask(`Strip unsupported frontmatter keys (${keys}) from ${affected.length} skill(s)?`))
  ) {
    log.warn(
      { skills: affected.length, keys },
      "keeping legacy-only frontmatter keys — pi ignores them (skill dependency chains are not supported)",
    );
    return;
  }

  for (const { path, scan } of affected) {
    await writeFile(path, scan.content, "utf8");
    log.info({ file: path, keys: scan.keys }, "stripped legacy-only frontmatter keys");
  }
};

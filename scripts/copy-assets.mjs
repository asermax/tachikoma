#!/usr/bin/env node
/**
 * Mirrors non-TS assets from src/ into dist/ — the prompt reference directories
 * (every "references" dir under src/, see src/agent/prompt-references.ts) and the built-in
 * skills (src/extensions/skills/builtin-skills) — because tsc only emits JavaScript.
 *
 * Run after every build (`just build`, `prepack`) so the compiled tree carries the
 * same asset layout as the source tree and `referencePointer`'s absolute paths
 * resolve identically from dist/. `collectAssetDirs` is exported for the parity
 * test (tests/scripts/copy-assets.test.ts).
 */
import { cp, mkdir, readdir } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Directory names treated as copy-whenever-found assets under src/. */
export const ASSET_DIR_NAMES = new Set(["references", "builtin-skills"]);

/**
 * Every asset directory under `srcRoot`: any directory whose name is in `names`,
 * found by recursive walk (asset dirs are not descended into). Skips dot-dirs and
 * node_modules. Sorted, so the output is deterministic.
 */
export const collectAssetDirs = async (srcRoot, names = ASSET_DIR_NAMES) => {
  const found = [];

  const walk = async (dir) => {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return; // unreadable or missing — nothing to collect
    }

    for (const entry of entries) {
      if (entry.name.startsWith(".") || entry.name === "node_modules") continue;

      const full = join(dir, entry.name);
      if (!entry.isDirectory()) continue;

      if (names.has(entry.name)) found.push(full);
      else await walk(full);
    }
  };

  await walk(srcRoot);
  return found.sort();
};

/** Mirror each collected dir to its same relative position under `outRoot`. */
export const mirrorAssetDirs = async (srcRoot, outRoot, dirs) => {
  for (const dir of dirs) {
    const target = join(outRoot, relative(srcRoot, dir));
    await mkdir(dirname(target), { recursive: true });
    await cp(dir, target, { recursive: true });
  }
};

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const srcRoot = join(packageRoot, "src");
  const outRoot = join(packageRoot, "dist");

  const dirs = await collectAssetDirs(srcRoot);
  await mirrorAssetDirs(srcRoot, outRoot, dirs);

  console.log(`copy-assets: mirrored ${dirs.length} asset dir(s) into dist/`);
}

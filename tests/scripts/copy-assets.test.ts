import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { collectAssetDirs, mirrorAssetDirs } from "../../scripts/copy-assets.mjs";

/**
 * The asset-copy parity guards (issue-445): the script is what makes `referencePointer`'s
 * absolute paths resolve inside an installed package (tsc only emits JS), so discovery must
 * find every asset dir and mirroring must preserve the relative layout exactly.
 */

const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

const tempTree = async (prefix: string): Promise<string> => {
  const dir = await mkdtemp(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
};

/** Every file under `dir`, as paths relative to `dir`. */
const relativeFiles = async (dir: string): Promise<string[]> => {
  const out: string[] = [];

  const walk = async (current: string) => {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else out.push(relative(dir, full));
    }
  };

  await walk(dir);
  return out.sort();
};

describe("collectAssetDirs", () => {
  it("finds references/ and builtin-skills/ wherever they appear, sorted", async () => {
    const root = await tempTree("tachi-assets-find-");
    for (const dir of [
      join(root, "src/agent/references"),
      join(root, "src/extensions/git/references"),
      join(root, "src/extensions/skills/builtin-skills/nested"),
      join(root, "src/extensions/skills/references"),
      join(root, "src/agent/sub/deeper/references"),
    ]) {
      await mkdir(dir, { recursive: true });
    }

    const found = await collectAssetDirs(root);

    expect(found).toEqual(
      [
        join(root, "src/agent/references"),
        join(root, "src/agent/sub/deeper/references"),
        join(root, "src/extensions/git/references"),
        join(root, "src/extensions/skills/builtin-skills"),
        join(root, "src/extensions/skills/references"),
      ].sort(),
    );
  });

  it("skips dot-directories and node_modules, and does not descend into asset dirs", async () => {
    const root = await tempTree("tachi-assets-skip-");
    await mkdir(join(root, "src/.git/references"), { recursive: true });
    await mkdir(join(root, "src/node_modules/pkg/references"), { recursive: true });
    // A references dir containing a nested references dir: collected once, not descended into.
    await mkdir(join(root, "src/extensions/x/references/references"), { recursive: true });

    const found = await collectAssetDirs(root);

    expect(found).toEqual([join(root, "src/extensions/x/references")]);
  });

  it("collects nothing from a missing root", async () => {
    expect(await collectAssetDirs(join(tmpdir(), "tachi-assets-missing-"))).toEqual([]);
  });
});

describe("mirrorAssetDirs", () => {
  it("mirrors each asset dir to its relative position with identical file sets (parity)", async () => {
    const root = await tempTree("tachi-assets-mirror-");
    const agentRefs = join(root, "src/agent/references");
    const gitRefs = join(root, "src/extensions/git/references");

    await mkdir(agentRefs, { recursive: true });
    await mkdir(gitRefs, { recursive: true });
    await writeFile(join(agentRefs, "conversation.md"), "# Conversation");
    await writeFile(join(gitRefs, "git.md"), "# Git");

    const outRoot = join(root, "dist");
    await mirrorAssetDirs(join(root, "src"), outRoot, await collectAssetDirs(join(root, "src")));

    expect(await relativeFiles(join(outRoot, "agent/references"))).toEqual(["conversation.md"]);
    expect(await relativeFiles(join(outRoot, "extensions/git/references"))).toEqual(["git.md"]);
  });
});

describe("the real source tree", () => {
  it("ships every asset dir the built tree needs — references for every section pointer plus the built-in skills", async () => {
    const srcRoot = join(import.meta.dirname, "..", "..", "src");
    const dirs = await collectAssetDirs(srcRoot);
    const relativeDirs = dirs.map((dir) => relative(srcRoot, dir));

    // The two core references the main base prompt points at.
    expect(relativeDirs).toContain(join("agent", "references"));
    // The built-in skills the skills extension registers as a pi skill source.
    expect(relativeDirs).toContain(join("extensions", "skills", "builtin-skills"));
    // Every first-party section's reference dir (kept in lockstep with the usage modules).
    for (const extension of [
      "boundary",
      "detached-processes",
      "external",
      "git",
      "memory",
      "notifications",
      "projects",
      "self-update",
      "skill-evolution",
      "skills",
      "tasks",
      "telegram",
      "workflows",
    ]) {
      expect(relativeDirs).toContain(join("extensions", extension, "references"));
    }
  });
});

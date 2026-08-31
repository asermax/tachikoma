import { readdir, readFile } from "node:fs/promises";
import { basename, join } from "node:path";

import { describe, expect, it } from "vitest";
import { collectAssetDirs } from "../../scripts/copy-assets.mjs";
import { referencePointer } from "../../src/agent/prompt-references.ts";
import { buildMainSystemPrompt } from "../../src/agent/prompts.ts";
import { ConfigSchema } from "../../src/config/schema.ts";
import { fileExists } from "../../src/util/markdown-store.ts";
import {
  EXTENSION_SECTION_RE,
  EXTENSION_SURFACES,
  LONG_TZ,
  listUsageModules,
  pointersOf,
  USAGE_SECTIONS,
} from "./extension-surfaces.ts";

/**
 * The two-tier documentation drift guards (issue-445): the static inline set stays small
 * (budget), every section points at reference files that actually exist, every reference
 * file is pointed to, and every usage module on disk is enumerated here. Placement judgments
 * (what to document, which tier) live in the placement matrix in
 * docs/feature-designs/foundational-context.md — these tests carry only the mechanically
 * checkable claims.
 */

const REPO_ROOT = join(import.meta.dirname, "..", "..");
const SRC_ROOT = join(REPO_ROOT, "src");

// The complete static inline set: the core base prompt plus every static usage constant
// (USAGE_SECTIONS). The completeness test below fails when a usage.ts exists outside it.
const STATIC_SECTIONS: Record<string, string> = {
  "core base prompt": buildMainSystemPrompt({
    workspaceRoot: "/workspace",
    dateHeader: `2026-08-31 (${LONG_TZ})`,
  }),
  ...USAGE_SECTIONS,
};

/**
 * The budget bounds inline CONTENT, so pointer lines are canonicalized before measuring: each
 * `Details: <abs path>` line embeds `import.meta.dirname`, whose length varies with where the
 * repo is checked out (CI's 37-char /home/runner/work/tachikoma/tachikoma root vs a 30-char
 * local clone — 7 chars × 15 pointers = the 105 chars that pushed CI past the cap with
 * byte-identical content). POSIX paths only, like the rest of this suite.
 */
const canonicalizeRoot = (text: string, root: string = REPO_ROOT): string =>
  text.replaceAll(`${root}/`, "/repo/");

/** The budget's unit: canonicalized content length. */
const measure = (text: string, root: string = REPO_ROOT): number =>
  canonicalizeRoot(text, root).length;

/** Every *.md file inside a references/ directory under `dir` (extensions + core). */
const listReferenceFiles = async (dir: string): Promise<string[]> => {
  const referenceDirs = (await collectAssetDirs(dir)).filter((d) => basename(d) === "references");
  const out: string[] = [];

  for (const referenceDir of referenceDirs) {
    for (const entry of await readdir(referenceDir)) {
      if (entry.endsWith(".md")) out.push(join(referenceDir, entry));
    }
  }

  return out.sort();
};

describe("referencePointer", () => {
  it("emits the canonical pointer line naming the reference file under references/", () => {
    expect(referencePointer("/pkg/dist/extensions/git", "git")).toBe(
      "Details: /pkg/dist/extensions/git/references/git.md (read on demand)",
    );
  });
});

describe("static inline set", () => {
  it("stays within the size budget — detail belongs in references, not inline", () => {
    const total = Object.values(STATIC_SECTIONS).reduce((sum, text) => sum + measure(text), 0);

    expect(
      total,
      "static inline set exceeds the size budget — move detail into reference files (see DES-014)",
    ).toBeLessThanOrEqual(10_500);
  });

  it("measures content, not the checkout path — totals are root-invariant", () => {
    const deepRoot = "/very/deep/synthetic/checkout/root";

    for (const [name, text] of Object.entries(STATIC_SECTIONS)) {
      const reRooted = text.replaceAll(`${REPO_ROOT}/`, `${deepRoot}/`); // as if cloned deeper
      const canonical = canonicalizeRoot(text);

      expect(reRooted, name).not.toBe(text); // the re-root really changed paths
      expect(measure(reRooted, deepRoot), name).toBe(canonical.length);
      // Nothing environment-shaped may survive canonicalization (a bare root with no trailing
      // path component would slip past the prefix replace and re-couple the gate to the clone).
      expect(canonical, name).not.toContain(REPO_ROOT);
    }
  });

  it("enumerates every usage.ts module under src/extensions", async () => {
    const usageSectionKeys = Object.keys(STATIC_SECTIONS).filter(
      (key) => key !== "core base prompt",
    );
    expect(usageSectionKeys.sort()).toEqual(await listUsageModules());
  });

  it("is headed guidance carrying at least one resolvable pointer", async () => {
    for (const [name, text] of Object.entries(STATIC_SECTIONS)) {
      // Usage sections open with "## "; the core base prompt opens with its date/identity
      // header but carries "## " sections — either way, headed guidance.
      expect(text.includes("## "), name).toBe(true);

      const pointers = pointersOf(text);
      expect(pointers.length, name).toBeGreaterThan(0);

      for (const pointer of pointers) {
        expect(await fileExists(pointer), `${name}: missing reference ${pointer}`).toBe(true);
      }
    }
  });

  it("covers every reference file under src/ — none may be orphaned", async () => {
    const pointedTo = new Set(Object.values(STATIC_SECTIONS).flatMap((text) => pointersOf(text)));
    const onDisk = await listReferenceFiles(SRC_ROOT);

    expect(onDisk.length).toBeGreaterThan(0);
    for (const file of onDisk) {
      expect(pointedTo, `unreferenced reference file: ${file}`).toContain(file);
    }
  });

  it("keeps the core reference files extension-agnostic (ownership rule)", async () => {
    // The core owns the conversation substrate only (issue-445): naming an extension's tools or
    // turn formats in src/agent/references/* drifts back toward a feature-coupled core prompt.
    // (The core's own <queued-notifications> digest wrapper is core-owned and allowed.)
    for (const file of await listReferenceFiles(join(SRC_ROOT, "agent"))) {
      const text = await readFile(file, "utf8");

      for (const surface of EXTENSION_SURFACES) {
        expect(text, `${file} references ${surface}`).not.toContain(surface);
      }

      // Config sections: the core may say per-feature `[extensions.<name>]` tables exist (the
      // `extensions` key is core schema) but may not document a named extension's knobs.
      expect(text, `${file} documents an extension's config`).not.toMatch(EXTENSION_SECTION_RE);
    }
  });
});

describe("core config reference", () => {
  // config.md is the one reference the core prompt points at for every option; its option
  // names must match the schema it describes (a renamed/added schema key with no doc update
  // fails here rather than misdirecting the agent).
  const CONFIG_REF = join(SRC_ROOT, "agent", "references", "config.md");

  it("names exactly the option keys the core ConfigSchema defines", async () => {
    const text = await readFile(CONFIG_REF, "utf8");

    // Dotted option tokens in the core-options table (`workspace.path`, `agent.main`, …).
    const documented = new Set([...text.matchAll(/`([a-z]+\.[a-zA-Z]+)`/g)].map((m) => m[1]));

    // Every dotted token must resolve to a real schema key.
    for (const option of documented) {
      const [section, key] = option.split(".");
      expect(Object.keys(ConfigSchema.properties ?? {}), option).toContain(section);
      expect(Object.keys(ConfigSchema.properties[section]?.properties ?? {}), option).toContain(
        key,
      );
    }

    // Every top-level schema section is mentioned somewhere in the reference.
    for (const section of Object.keys(ConfigSchema.properties ?? {})) {
      expect(text, `config.md never mentions [${section}]`).toContain(section);
    }
  });
});

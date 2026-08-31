import { type Dirent, readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { referencePointer } from "../../src/agent/prompt-references.ts";
import { buildMainSystemPrompt } from "../../src/agent/prompts.ts";
import { ConfigSchema } from "../../src/config/schema.ts";
import { BOUNDARY_USAGE } from "../../src/extensions/boundary/usage.ts";
import { DETACHED_PROCESSES_USAGE } from "../../src/extensions/detached-processes/usage.ts";
import { EXTERNAL_USAGE } from "../../src/extensions/external/usage.ts";
import { GIT_USAGE } from "../../src/extensions/git/usage.ts";
import { MEMORY_LAYOUT_USAGE } from "../../src/extensions/memory/usage.ts";
import { NOTIFICATIONS_USAGE } from "../../src/extensions/notifications/usage.ts";
import { PROJECTS_USAGE } from "../../src/extensions/projects/usage.ts";
import { SELF_UPDATE_USAGE } from "../../src/extensions/self-update/usage.ts";
import { SKILL_EVOLUTION_USAGE } from "../../src/extensions/skill-evolution/usage.ts";
import { SKILLS_USAGE } from "../../src/extensions/skills/usage.ts";
import { buildTasksUsage } from "../../src/extensions/tasks/usage.ts";
import { TELEGRAM_USAGE } from "../../src/extensions/telegram/usage.ts";
import { WORKFLOWS_USAGE } from "../../src/extensions/workflows/usage.ts";
import { EXTENSION_SECTION_RE, EXTENSION_SURFACES } from "./extension-surfaces.ts";

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

/** The pointer line's exact shape — one source of truth, `referencePointer`. */
const POINTER_RE = /^Details: (.+) \(read on demand\)$/gm;

const pointersOf = (text: string): string[] => [...text.matchAll(POINTER_RE)].map((m) => m[1]);

// The complete static inline set: the core base prompt plus one entry per static usage
// constant. The completeness test below fails when a usage.ts exists outside this list.
// Measured with a deliberately long configured timezone: the date header and the tasks section
// both embed it, so a short "UTC" undercounts what real deployments ship.
const LONG_TZ = "America/Argentina/Buenos_Aires";

const STATIC_SECTIONS: Record<string, string> = {
  "core base prompt": buildMainSystemPrompt({
    workspaceRoot: "/workspace",
    dateHeader: `2026-08-31 (${LONG_TZ})`,
  }),
  "boundary/usage.ts": BOUNDARY_USAGE,
  "detached-processes/usage.ts": DETACHED_PROCESSES_USAGE,
  "external/usage.ts": EXTERNAL_USAGE,
  "git/usage.ts": GIT_USAGE,
  "memory/usage.ts": MEMORY_LAYOUT_USAGE,
  "notifications/usage.ts": NOTIFICATIONS_USAGE,
  "projects/usage.ts": PROJECTS_USAGE,
  "self-update/usage.ts": SELF_UPDATE_USAGE,
  "skill-evolution/usage.ts": SKILL_EVOLUTION_USAGE,
  "skills/usage.ts": SKILLS_USAGE,
  "tasks/usage.ts": buildTasksUsage(LONG_TZ),
  "telegram/usage.ts": TELEGRAM_USAGE,
  "workflows/usage.ts": WORKFLOWS_USAGE,
};

/** Every usage.ts module under src/extensions — the sweep must enumerate all of them. */
const listUsageModules = async (): Promise<string[]> => {
  const entries = await readdir(join(SRC_ROOT, "extensions"), { withFileTypes: true });
  const modules: string[] = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;

    try {
      await stat(join(SRC_ROOT, "extensions", entry.name, "usage.ts"));
      modules.push(`${entry.name}/usage.ts`);
    } catch {
      // no usage module — fine, the matrix records the placement decision
    }
  }

  return modules.sort();
};

/** Every *.md file inside a references/ directory under `dir` (extensions + core). */
const listReferenceFiles = async (dir: string): Promise<string[]> => {
  const out: string[] = [];
  let entries: Dirent[];

  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return out;
  }

  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await listReferenceFiles(full)));
    else if (dir.endsWith("references") && entry.name.endsWith(".md")) out.push(full);
  }

  return out.sort();
};

const fileExists = async (path: string): Promise<boolean> => {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
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
    const total = Object.values(STATIC_SECTIONS).reduce((sum, text) => sum + text.length, 0);

    expect(total).toBeLessThanOrEqual(10_500);
  });

  it("enumerates every usage.ts module under src/extensions", async () => {
    expect(Object.keys(STATIC_SECTIONS).slice(1).sort()).toEqual(await listUsageModules());
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

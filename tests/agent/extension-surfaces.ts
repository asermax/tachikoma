import { readdirSync } from "node:fs";
import { readdir, stat } from "node:fs/promises";
import { join } from "node:path";

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

/** Escape a literal for embedding in a RegExp source (dir names are kebab-case, but be safe). */
const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * Extension surfaces that must never appear in core-owned prompt material (issue-445's
 * ownership rule): the core base prompt and `src/agent/references/*` document only the
 * conversation substrate, so naming an extension's tools or turn formats there is drift back
 * toward a feature-coupled core prompt. Shared by the prompt and prompt-references suites so
 * a new tool is added once.
 */
export const EXTENSION_SURFACES = [
  "delegate_to_agent",
  "ask_branch",
  "respond_to_task",
  "notify_user",
  "update_goal",
  "upgrade_self",
  "restart_self",
  "commit_workspace",
  "scrub",
  "register_project",
  "deregister_project",
  "list_projects",
  "dispatch_detached_process",
  "query_process",
  "read_process_output",
  "terminate_process",
  "install_extension",
  "update_extension",
  "uninstall_extension",
  "list_installed_extensions",
  "start_workflow",
  "update_workflow_state",
  "end_workflow",
  "query_workflow",
  "send_telegram_file",
  "react_to_message",
  "pin_message",
  "unpin_message",
  "send_message_with_buttons",
  "create_task",
  "update_task",
  "run_task_now",
  "branch_summary",
  "📋 Scheduled task",
];

/**
 * A core reference may say that per-feature `[extensions.<name>]` tables exist (the
 * `extensions` key is part of the core ConfigSchema) but may not document a specific
 * extension's knobs — that guidance belongs to the owning extension's own reference.
 * The names are derived from `src/extensions/` so a newly added first-party extension
 * is covered without remembering to extend a hand-maintained list.
 */
const extensionDirNames = readdirSync(join(import.meta.dirname, "..", "..", "src", "extensions"), {
  withFileTypes: true,
})
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();

export const EXTENSION_SECTION_RE = new RegExp(
  `\\[extensions\\.(${extensionDirNames.map(escapeRegExp).join("|")})\\]`,
);

/**
 * The pointer line's exact shape — owned by `referencePointer`
 * (src/agent/prompt-references.ts). Tests resolve a section's reference file through its
 * own pointer instead of re-encoding the `references/<topic>.md` layout.
 */
export const POINTER_RE = /^Details: (.+) \(read on demand\)$/gm;

export const pointersOf = (text: string): string[] =>
  [...text.matchAll(POINTER_RE)].map((m) => m[1]);

/**
 * A deliberately long configured timezone: the core prompt's date header and the tasks
 * usage section both embed it, so a short "UTC" undercounts what real deployments ship
 * when measuring the static inline set.
 */
export const LONG_TZ = "America/Argentina/Buenos_Aires";

/**
 * The one static usage-section enumeration (issue-445): every usage module that reaches
 * the prompt as a string constant, keyed by its module path. The drift sweep
 * (tests/agent/prompt-references.test.ts), the content table (tests/usage-sections.test.ts),
 * and the asset parity test (tests/scripts/copy-assets.test.ts) all read from here or from
 * `listUsageModules`, so a new usage.ts is wired in exactly one place.
 */
export const USAGE_SECTIONS = {
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

/** Every usage.ts module under src/extensions — the sweeps must enumerate all of them. */
export const listUsageModules = async (): Promise<string[]> => {
  const extensionsRoot = join(import.meta.dirname, "..", "..", "src", "extensions");
  const entries = await readdir(extensionsRoot, { withFileTypes: true });
  const modules: string[] = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;

    try {
      await stat(join(extensionsRoot, entry.name, "usage.ts"));
      modules.push(`${entry.name}/usage.ts`);
    } catch {
      // no usage module — fine, the placement matrix records the placement decision
    }
  }

  return modules.sort();
};

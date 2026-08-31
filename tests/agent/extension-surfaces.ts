import { readdirSync } from "node:fs";
import { join } from "node:path";

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

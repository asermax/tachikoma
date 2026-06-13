import { type ExtensionFactory, isToolCallEventType } from "@earendil-works/pi-coding-agent";

import type { Logger } from "../../log.ts";

/**
 * Destructive / irrecoverable git operations the agent must not run through its
 * bash tool. Matched (anchored at the start) against each sub-command of a
 * compound command. The sanctioned tools (commit_workspace, scrub, and the
 * session-close commit/push) own these operations instead. `git clone` and
 * read-only subcommands are deliberately not matched.
 */
export const DESTRUCTIVE_GIT_DENY_PATTERNS: RegExp[] = [
  /^git\s+push\b/,
  /^git\s+reset\b/,
  /^git\s+(checkout|restore)\s+\.(\s|$)/,
  /^git\s+clean\b/,
  /^git\s+remote\s+(add|remove|rm|rename|set-url|set-head|set-branches|prune)\b/,
  /^git\s+filter-repo\b/,
  /^git\s+rebase\b/,
];

/**
 * Split a shell command on the compound operators `&&`, `||`, `|`, and `;`,
 * only when they appear outside single quotes, double quotes, and backslash
 * escapes. This avoids false splits on a `|` inside a quoted argument (e.g.
 * `grep -E "a|b"`), which would otherwise let a destructive command hide
 * behind quoting.
 */
export const splitCompoundCommands = (command: string): string[] => {
  const parts: string[] = [];
  let current = "";
  let inSingleQuote = false;
  let inDoubleQuote = false;
  let i = 0;

  while (i < command.length) {
    const char = command[i];

    if (inSingleQuote) {
      current += char;

      if (char === "'") inSingleQuote = false;
    } else if (inDoubleQuote) {
      if (char === "\\" && i + 1 < command.length) {
        current += char + command[i + 1];
        i += 2;
        continue;
      }

      current += char;

      if (char === '"') inDoubleQuote = false;
    } else if (char === "'") {
      current += char;
      inSingleQuote = true;
    } else if (char === '"') {
      current += char;
      inDoubleQuote = true;
    } else if (char === "\\" && i + 1 < command.length) {
      current += char + command[i + 1];
      i += 2;
      continue;
    } else if (char === ";" || char === "|" || char === "&") {
      const next = command[i + 1];
      const isTwoChar = (char === "|" || char === "&") && next === char;
      const splits = char === ";" || char === "|" || isTwoChar;

      if (splits) {
        if (current.trim() !== "") parts.push(current.trim());

        current = "";

        if (isTwoChar) i += 1;
      } else {
        current += char;
      }
    } else {
      current += char;
    }

    i += 1;
  }

  if (current.trim() !== "") parts.push(current.trim());

  return parts;
};

export interface DeniedMatch {
  subcommand: string;
  pattern: RegExp;
}

/**
 * Return the first sub-command that matches any deny pattern, or null when the
 * whole command is clear. A single destructive sub-command condemns the entire
 * compound command.
 */
export const findDeniedSubcommand = (
  command: string,
  patterns: RegExp[] = DESTRUCTIVE_GIT_DENY_PATTERNS,
): DeniedMatch | null => {
  for (const subcommand of splitCompoundCommands(command)) {
    for (const pattern of patterns) {
      if (pattern.test(subcommand)) return { subcommand, pattern };
    }
  }

  return null;
};

const denialReason = (match: DeniedMatch): string =>
  `Destructive git command blocked: \`${match.subcommand}\`. ` +
  "The agent must not run destructive or history-rewriting git via bash. " +
  "Use the dedicated tools instead: commit_workspace to save changes, scrub to " +
  "purge paths from history, and the automatic session-close commit/push for syncing.";

/**
 * pi extension factory that intercepts bash tool calls and blocks any compound
 * command containing a destructive git sub-command, steering the agent toward
 * the sanctioned git tools. Other bash commands pass through untouched.
 */
export const createGitGuardrailFactory =
  (log: Logger): ExtensionFactory =>
  (pi) => {
    pi.on("tool_call", (event) => {
      if (!isToolCallEventType("bash", event)) return;

      const match = findDeniedSubcommand(event.input.command);

      if (match == null) return;

      log.warn(
        { command: event.input.command, pattern: match.pattern.source },
        "blocked destructive git bash command",
      );

      return { block: true, reason: denialReason(match) };
    });
  };

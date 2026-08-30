import { truncateTail } from "@earendil-works/pi-coding-agent";

import type { SideRunner } from "../agent/side-run.ts";
import { runGitCapture } from "./git.ts";

/**
 * Mechanical plumbing shared by every headless git-driving agent (the commit agent, the rebase
 * resolver, the skill-evolution proposal agent): the side-runner slice, the allowlist they have
 * in common, tool-result text shaping, and unattended git execution. Policy — each agent's own
 * allowlist beyond this set, path validation, refusal wording — stays with the agent.
 */

/** The side-runner slice every headless git-driving agent needs. */
export type AgentRunner = Pick<SideRunner, "run">;

/**
 * The only git sub-commands an agent that may stage and commit is allowed to run: inspect
 * state, stage, and commit. Everything else (push, fetch, reset, rebase, clean, remote,
 * filter-repo, checkout/restore) is refused so the agent can only add commits, never mutate
 * history or remote state. This is the legacy `GIT_BASH_HOOK` allowlist.
 */
export const ALLOWED_GIT_SUBCOMMANDS = new Set(["status", "diff", "log", "add", "commit", "show"]);

/** Tool-result text with tail truncation — the shared shape every agent tool returns. */
export const textResult = (text: string) => {
  const { content, truncated } = truncateTail(text);

  return {
    content: [{ type: "text" as const, text: truncated ? `${content}\n\n[truncated]` : content }],
    details: undefined,
  };
};

/** A refusal is instructive text, so the agent self-corrects within the same run. */
export const refusal = (reason: string, guidance: string) =>
  textResult(`Refused: ${reason} ${guidance}`);

/**
 * Run git the way an unattended agent tool must — `core.editor=true` so nothing blocks waiting
 * on an editor, plus the caller's extra config (`commit.gpgsign=false` for committing agents,
 * `sequence.editor=true` for the rebase resolver) — and shape the result as the
 * exit/stdout/stderr text every such tool reports.
 */
export const runGitTool = async (
  cwd: string,
  args: readonly string[],
  extraConfig: readonly string[] = [],
): Promise<string> => {
  const result = await runGitCapture(cwd, [
    "-c",
    "core.editor=true",
    ...extraConfig.flatMap((value) => ["-c", value]),
    ...args,
  ]);

  return `exit ${result.code}\n${[result.stdout, result.stderr].filter(Boolean).join("\n")}`.trim();
};

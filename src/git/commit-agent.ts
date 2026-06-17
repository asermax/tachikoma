import { readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";

import { type ToolDefinition, truncateTail } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { SideRunner } from "../agent/side-run.ts";
import type { Logger } from "../log.ts";
import { runGitCapture } from "./git.ts";

export type AgentRunner = Pick<SideRunner, "run">;

/**
 * Drives an agent to commit the uncommitted changes in `cwd` as one or more
 * cohesive commits. Returns nothing — whether it actually committed (and what
 * the subjects were) is decided by the caller (`commitAll`) from the on-disk
 * git state, never from the agent's own report. The git extension and the
 * projects extension supply an agent-backed implementation.
 */
export type CommitAgent = (cwd: string, log: Logger) => Promise<void>;

/**
 * The only git sub-commands the commit agent may run: inspect state, stage, and
 * commit. Everything else (push, fetch, reset, rebase, clean, remote,
 * filter-repo, checkout/restore) is refused so the agent can only add commits,
 * never mutate history or remote state. This is the legacy `GIT_BASH_HOOK`
 * allowlist.
 */
const ALLOWED_GIT_SUBCOMMANDS = new Set(["status", "diff", "log", "add", "commit", "show"]);

const ReadFileParams = Type.Object({
  path: Type.String({ description: "Repo-relative (or absolute) path of the file to read" }),
});

const GitParams = Type.Object({
  args: Type.Array(Type.String(), {
    description:
      'git arguments, e.g. ["status"], ["diff"], ["log","--oneline","-10"], ["add","path"], ["commit","-m","message"]',
    minItems: 1,
  }),
});

const resolvePath = (cwd: string, path: string): string =>
  isAbsolute(path) ? path : resolve(cwd, path);

const textResult = (text: string) => {
  const { content, truncated } = truncateTail(text);

  return {
    content: [{ type: "text" as const, text: truncated ? `${content}\n\n[truncated]` : content }],
    details: undefined,
  };
};

/**
 * Build the cwd-scoped tool set the commit agent uses. The tools are bound to
 * one repo so the agent can never operate on the wrong tree (the side session
 * itself always runs at the workspace root), and `git` only permits the
 * inspection/stage/commit sub-commands — it can add commits, nothing more.
 * `core.editor=true` keeps a commit without an editor prompt from blocking, and
 * `commit.gpgsign=false` lets unattended commits succeed in repos with signing
 * configured.
 */
const buildCommitTools = (cwd: string): ToolDefinition[] => [
  {
    name: "read_file",
    label: "Read file",
    description:
      "Read a file from the repository being committed (e.g. to inspect a change, CONTRIBUTING.md, CLAUDE.md, or AGENTS.md).",
    parameters: ReadFileParams,
    async execute(_id, params) {
      return textResult(await readFile(resolvePath(cwd, params.path), "utf8"));
    },
  } satisfies ToolDefinition<typeof ReadFileParams>,

  {
    name: "git",
    label: "Run git",
    description:
      "Run a git command in the repository being committed. Only status, diff, log, add, commit, and show are allowed — push, reset, rebase, clean, remote, and filter-repo are rejected.",
    parameters: GitParams,
    async execute(_id, params) {
      const subcommand = params.args[0];

      if (subcommand == null || !ALLOWED_GIT_SUBCOMMANDS.has(subcommand)) {
        return textResult(
          `Refused: git ${subcommand ?? ""} is not allowed while committing. ` +
            "Use only status, diff, log, add, commit, or show.",
        );
      }

      const result = await runGitCapture(cwd, [
        "-c",
        "core.editor=true",
        "-c",
        "commit.gpgsign=false",
        ...params.args,
      ]);

      return textResult(
        `exit ${result.code}\n${[result.stdout, result.stderr].filter(Boolean).join("\n")}`.trim(),
      );
    },
  } satisfies ToolDefinition<typeof GitParams>,
];

/** Shared constraints appended to every commit-agent system prompt. */
const SAFETY =
  "You may only use status, diff, log, add, commit, and show. Never push, amend, reset, rebase, clean, or rewrite history, and never touch the .git directory. When you are done, report what you committed.";

/** Per-mode system prompt and instruction. The mode is fixed at agent creation. */
const MODE_CONFIG = {
  workspace: {
    system: `You commit workspace changes for an automated workspace-versioning agent, grouping related changes into separate, descriptive commits.

The workspace holds: memories (episodic summaries, topic and learning notes), context files (SOUL.md, USER.md, AGENTS.md), configuration, transcripts, and project submodule pointers.

Steps:
1. Run git status and git diff to see every change.
2. Group changes into cohesive sets by subdirectory or purpose — for example episodic memories together, topic notes together, context-file edits together, configuration together. Do NOT lump unrelated areas into a single commit.
3. For each group: git add <the specific paths>, then git commit -m "<imperative, descriptive message under 72 characters>".
4. Run git status again. If anything remains uncommitted, commit it.
5. Stop when the working tree is clean.

${SAFETY}`,
    prompt:
      "Commit the workspace's uncommitted changes as one or more cohesive commits grouped by area, then verify the working tree is clean.",
  },
  project: {
    system: `You commit changes in an external project repository, matching that project's own commit-message conventions.

Steps:
1. Run git log --oneline -10 to learn this project's commit-message style (conventional commits, semantic prefixes, plain prose, etc.).
2. Read CONTRIBUTING.md, CLAUDE.md, or AGENTS.md at the repository root if present, for documented conventions.
3. Run git status and git diff to see every change.
4. Group related changes into cohesive sets by area or purpose. Match the project's established commit style in every message.
5. For each group: git add <the specific paths>, then git commit -m "<message matching the project style>".
6. Run git status again; commit anything remaining. Stop when the working tree is clean.

${SAFETY}`,
    prompt:
      "Commit this project's uncommitted changes as one or more cohesive commits that match its existing style, then verify the working tree is clean.",
  },
} as const;

/**
 * A `CommitAgent` backed by a headless side agent. The agent inspects the diff,
 * groups changes, and creates one commit per group through cwd-scoped tools;
 * whether it committed (and the resulting subjects) is decided by the caller
 * from git state, not by trusting the agent. Errors propagate so the caller can
 * fall back to a single deterministic commit.
 */
export const createCommitAgent =
  (side: AgentRunner, mode: "workspace" | "project"): CommitAgent =>
  async (cwd, log) => {
    const { system, prompt } = MODE_CONFIG[mode];

    log.info({ path: cwd, mode }, "spawning commit agent");

    const startedAt = Date.now();

    await side.run({
      system,
      prompt,
      customTools: buildCommitTools(cwd),
      tier: "processor",
    });

    log.debug({ path: cwd, mode, durationMs: Date.now() - startedAt }, "commit agent finished");
  };

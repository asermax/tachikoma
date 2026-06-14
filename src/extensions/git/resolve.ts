import { readFile, writeFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";

import { type ToolDefinition, truncateTail } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import type { SideRunner } from "../../agent/side-run.ts";
import { runGitCapture } from "../../git/git.ts";
import type { RebaseResolver } from "../../git/sync.ts";

export type AgentRunner = Pick<SideRunner, "run">;

const CONFLICT_SYSTEM_PROMPT = `You resolve git rebase conflicts for an automated workspace-versioning agent.

A rebase is already in progress and has stopped on a conflict. Your job is to
drive it to completion using ONLY the tools provided to you (read_conflict,
write_resolved, git). Every tool operates on the one repository you were given —
you do not choose a directory.

For each conflicted file:
1. Read it with read_conflict to see the conflict markers (<<<<<<<, =======, >>>>>>>).
2. Produce a coherent merge of BOTH sides — never blindly discard one side, and
   remove every conflict marker. Save it with write_resolved.
3. Stage it: git with args ["add", "<path>"].
4. Continue the rebase: git with args ["rebase", "--continue"]. The editor is
   disabled, so commit messages are accepted as-is.
5. Repeat for any further conflicts until the rebase finishes.

If the changes are genuinely incompatible and cannot be merged, run
git with args ["rebase", "--abort"] and stop.

Never run git push. Never touch files unrelated to the conflict.
When the rebase has finished (or you have aborted), stop and report what you did.`;

const ReadConflictParams = Type.Object({
  path: Type.String({ description: "Repo-relative (or absolute) path of the conflicted file" }),
});

const WriteResolvedParams = Type.Object({
  path: Type.String({ description: "Repo-relative (or absolute) path of the file to overwrite" }),
  content: Type.String({ description: "Full resolved file contents with no conflict markers" }),
});

const GitParams = Type.Object({
  args: Type.Array(Type.String(), {
    description: 'git arguments, e.g. ["add", "file.txt"] or ["rebase", "--continue"]',
    minItems: 1,
  }),
});

const FORBIDDEN_GIT_SUBCOMMANDS = new Set(["push", "fetch", "remote", "filter-repo", "reset"]);

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
 * Build the cwd-scoped tool set the resolver agent uses. The tools are bound to
 * one repo so the agent can never operate on the wrong tree (the side session
 * itself always runs at the workspace root), and `git` rejects the operations
 * that would push, fetch, or rewrite remote state — the agent only touches the
 * in-progress rebase.
 */
const buildResolverTools = (cwd: string): ToolDefinition[] => [
  {
    name: "read_conflict",
    label: "Read conflicted file",
    description: "Read a file from the repository being rebased, including its conflict markers.",
    parameters: ReadConflictParams,
    async execute(_id, params) {
      return textResult(await readFile(resolvePath(cwd, params.path), "utf8"));
    },
  } satisfies ToolDefinition<typeof ReadConflictParams>,

  {
    name: "write_resolved",
    label: "Write resolved file",
    description:
      "Overwrite a file in the repository being rebased with its fully resolved content.",
    parameters: WriteResolvedParams,
    async execute(_id, params) {
      await writeFile(resolvePath(cwd, params.path), params.content, "utf8");
      return textResult(`Wrote ${params.path}`);
    },
  } satisfies ToolDefinition<typeof WriteResolvedParams>,

  {
    name: "git",
    label: "Run git",
    description:
      "Run a git command in the repository being rebased (e.g. add, rebase --continue, rebase --abort, status, diff). Push, fetch, reset, and remote operations are rejected.",
    parameters: GitParams,
    async execute(_id, params) {
      const subcommand = params.args[0];

      if (subcommand != null && FORBIDDEN_GIT_SUBCOMMANDS.has(subcommand)) {
        return textResult(`Refused: git ${subcommand} is not allowed during conflict resolution.`);
      }

      const result = await runGitCapture(cwd, [
        "-c",
        "core.editor=true",
        "-c",
        "sequence.editor=true",
        ...params.args,
      ]);

      return textResult(
        `exit ${result.code}\n${[result.stdout, result.stderr].filter(Boolean).join("\n")}`.trim(),
      );
    },
  } satisfies ToolDefinition<typeof GitParams>,
];

/**
 * A `RebaseResolver` backed by a headless side agent. The agent drives the
 * in-progress rebase through cwd-scoped tools; whether the rebase finished is
 * decided by the caller from filesystem state, not by trusting the agent.
 */
export const createGitResolver =
  (side: AgentRunner): RebaseResolver =>
  async (cwd, remoteBranch, log) => {
    log.info({ path: cwd, remoteBranch }, "spawning rebase conflict resolution agent");

    try {
      await side.run({
        system: CONFLICT_SYSTEM_PROMPT,
        prompt: `Resolve the in-progress rebase onto ${remoteBranch} and continue it until it completes. If unresolvable, abort it.`,
        customTools: buildResolverTools(cwd),
        tier: "processor",
      });
    } catch (error) {
      log.warn({ path: cwd, err: error }, "rebase conflict resolution agent failed");
    }
  };

import { type ExtensionFactory, truncateTail } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import { type Completer, commitAll } from "./commit.ts";
import { runGitCapture } from "./git.ts";
import { workspaceFallbackMessage } from "./processor.ts";

export interface GitToolDeps {
  workspaceRoot: string;
  side: Completer;
  log: Logger;
}

export const QueryGitStatusParams = Type.Object({});

export const ListRecentCommitsParams = Type.Object({
  limit: Type.Optional(
    Type.Number({ description: "Maximum number of commits to return (default 10)" }),
  ),
});

export const CommitWorkspaceParams = Type.Object({
  message: Type.Optional(
    Type.String({
      description: "Commit message to use; when omitted, a message is generated from the changes",
    }),
  ),
});

export const handleQueryGitStatus = async ({ workspaceRoot }: GitToolDeps): Promise<string> => {
  const branch = await runGitCapture(workspaceRoot, ["symbolic-ref", "--short", "HEAD"]);
  const header =
    branch.code === 0 && branch.stdout !== ""
      ? `On branch ${branch.stdout}`
      : "Detached HEAD or unborn branch";

  const status = await runGitCapture(workspaceRoot, ["status", "--porcelain"]);

  if (status.code !== 0) {
    throw new Error(`git status failed: ${status.stderr || `exit code ${status.code}`}`);
  }

  if (status.stdout === "") return `${header}\n\nWorking tree is clean.`;

  const { content } = truncateTail(status.stdout);

  return `${header}\n\nUncommitted changes:\n${content}`;
};

export const handleListRecentCommits = async (
  { workspaceRoot }: GitToolDeps,
  args: Static<typeof ListRecentCommitsParams>,
): Promise<string> => {
  const limit = args.limit ?? 10;
  const result = await runGitCapture(workspaceRoot, [
    "log",
    "-n",
    String(limit),
    "--date=short",
    "--format=%h %ad %s",
  ]);

  if (result.code !== 0 || result.stdout === "") return "No commits found.";

  const { content } = truncateTail(result.stdout);

  return `Recent commits:\n${content}`;
};

export const handleCommitWorkspace = async (
  { workspaceRoot, side, log }: GitToolDeps,
  args: Static<typeof CommitWorkspaceParams>,
): Promise<string> => {
  const message = await commitAll({
    cwd: workspaceRoot,
    side,
    fallbackMessage: workspaceFallbackMessage(),
    ...(args.message != null ? { message: args.message } : {}),
    log,
  });

  if (message == null) return "Nothing to commit — the working tree is clean.";

  return `Committed workspace changes: ${message}`;
};

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** pi extension factory exposing the workspace git tools to the agent. */
export const createGitToolsFactory =
  (deps: GitToolDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "query_git_status",
      label: "Query Git Status",
      description:
        "Show the workspace git status: current branch and any uncommitted changes (modified, added, deleted, untracked files).",
      promptSnippet: "Inspect uncommitted changes in the workspace git repo",
      promptGuidelines: [
        "Use query_git_status to check for pending workspace changes before committing.",
      ],
      parameters: QueryGitStatusParams,
      async execute() {
        return textResult(await handleQueryGitStatus(deps));
      },
    });

    pi.registerTool({
      name: "list_recent_commits",
      label: "List Recent Commits",
      description:
        "List the most recent commits in the workspace git repo (hash, date, and subject), newest first.",
      promptSnippet: "Review recent workspace commit history",
      promptGuidelines: [
        "Use list_recent_commits when the user asks what changed in the workspace recently.",
      ],
      parameters: ListRecentCommitsParams,
      async execute(_toolCallId, params) {
        return textResult(await handleListRecentCommits(deps, params));
      },
    });

    pi.registerTool({
      name: "commit_workspace",
      label: "Commit Workspace",
      description:
        "Stage and commit all pending workspace changes in a single commit. A descriptive message is generated from the changes unless one is provided. Changes are also committed automatically at session end — use this only when an immediate commit matters.",
      promptSnippet: "Commit pending workspace changes on demand",
      promptGuidelines: [
        "Use commit_workspace when the user explicitly asks to save or commit workspace changes now.",
      ],
      parameters: CommitWorkspaceParams,
      async execute(_toolCallId, params) {
        return textResult(await handleCommitWorkspace(deps, params));
      },
    });
  };

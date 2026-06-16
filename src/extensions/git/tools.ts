import { access } from "node:fs/promises";
import { join } from "node:path";
import { type ExtensionFactory, truncateTail } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";
import { commitAll } from "../../git/commit.ts";
import type { CommitAgent } from "../../git/commit-agent.ts";
import { hasRemote, listSubmodules, runGitCapture } from "../../git/git.ts";
import {
  PUSH_RESULT,
  PUSH_SUCCESS,
  type PushResult,
  type RebaseResolver,
  smartPush,
} from "../../git/sync.ts";
import type { Logger } from "../../log.ts";
import { workspaceFallbackMessage } from "./processor.ts";
import { SCRUB_RESULT, scrubPaths } from "./scrub.ts";

export interface GitToolDeps {
  workspaceRoot: string;
  agent: CommitAgent;
  log: Logger;
  resolver?: RebaseResolver;
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
      description:
        "Fallback commit message, used only if automatic grouped committing fails. Omit to let changes be grouped into descriptive commits (with a deterministic dated fallback).",
    }),
  ),
  push: Type.Optional(
    Type.Boolean({
      description:
        "Push to origin after committing — the workspace and any project submodules with commits ahead of their remote (even when the working tree is clean). Default true; set false to commit only.",
    }),
  ),
});

export const ScrubWorkspaceParams = Type.Object({
  paths: Type.Array(Type.String(), {
    description: "File or directory paths to permanently remove from the entire git history",
    minItems: 1,
  }),
  project: Type.Optional(
    Type.String({
      description:
        "Project name under projects/ to scrub paths from. Omit to scrub the workspace repository itself.",
    }),
  ),
});

const exists = async (path: string): Promise<boolean> =>
  access(path).then(
    () => true,
    () => false,
  );

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

/** Map a `smartPush` outcome to a one-line, human-readable summary (null = omit). */
const describePush = (label: string, result: PushResult): string | null => {
  if (PUSH_SUCCESS.has(result)) return `Pushed ${label} to origin.`;
  if (result === PUSH_RESULT.nothingToPush) return null;

  const capital = label.charAt(0).toUpperCase() + label.slice(1);

  return `${capital} push failed — changes remain committed locally.`;
};

/**
 * Push a single repo to `origin` (when one is configured) via `smartPush`, with
 * agent-assisted conflict resolution when a resolver is wired. Returns a summary
 * line, or null when there is no remote or nothing to push. Never throws — a
 * failed push surfaces as a line and leaves the commits local.
 */
const pushRepo = async (
  cwd: string,
  label: string,
  resolver: RebaseResolver | undefined,
  log: Logger,
): Promise<string | null> => {
  if (!(await hasRemote(cwd, "origin"))) return null;

  try {
    const result = await smartPush(cwd, "origin", "HEAD", log, resolver);

    if (PUSH_SUCCESS.has(result)) {
      log.info({ path: cwd, result }, `commit_workspace pushed ${label}`);
    } else if (result !== PUSH_RESULT.nothingToPush) {
      log.warn({ path: cwd, result }, `commit_workspace push failed for ${label}`);
    }

    return describePush(label, result);
  } catch (error) {
    // `smartPush`/`hasRemote` never throw by contract; this catch guarantees
    // `pushRepo` resolves rather than rejects, so the parallel batch in
    // `pushWorkspaceAndSubmodules` can use `Promise.all` and one bad repo can't
    // abort its siblings. Surface the impossible throw as the same failure line
    // an enum failure would produce.
    log.warn({ path: cwd, err: error }, `commit_workspace push errored for ${label}`);
    return describePush(label, PUSH_RESULT.pushFailed);
  }
};

/**
 * Push the workspace repo and every registered project submodule to its
 * `origin`. All repos push in parallel — the submodule pointer is committed
 * before this runs, so they're independent — and `pushRepo` never rejects, so a
 * failing repo can't abort its siblings. Lines come back in workspace-then-
 * `listSubmodules` order; a repo with no remote or nothing to push yields none.
 */
const pushWorkspaceAndSubmodules = async (
  workspaceRoot: string,
  resolver: RebaseResolver | undefined,
  log: Logger,
): Promise<string[]> => {
  const submodulePaths = await listSubmodules(workspaceRoot);

  const targets = [
    { cwd: workspaceRoot, label: "workspace" },
    ...submodulePaths.map((path) => ({
      cwd: join(workspaceRoot, path),
      label: `project '${path.split("/").at(-1) ?? path}'`,
    })),
  ];

  const results = await Promise.all(
    targets.map(({ cwd, label }) => pushRepo(cwd, label, resolver, log)),
  );

  return results.filter((line): line is string => line != null);
};

export const handleCommitWorkspace = async (
  { workspaceRoot, agent, log, resolver }: GitToolDeps,
  args: Static<typeof CommitWorkspaceParams>,
): Promise<string> => {
  const subjects = await commitAll({
    agent,
    cwd: workspaceRoot,
    fallbackMessage: args.message ?? workspaceFallbackMessage(),
    log,
  });

  const lines: string[] = [];

  if (subjects.length === 1) {
    lines.push(`Committed workspace changes: ${subjects[0] ?? "(no message)"}`);
  } else if (subjects.length > 1) {
    lines.push(`Committed ${subjects.length} workspace changes:\n- ${subjects.join("\n- ")}`);
  } else {
    lines.push("Nothing to commit — the working tree is clean.");
  }

  // Push even when nothing was committed: a clean tree can still be ahead of its
  // remote (commits made earlier, a prior push that failed) — that's the case
  // this tool exists to handle.
  if (args.push ?? true) {
    lines.push(...(await pushWorkspaceAndSubmodules(workspaceRoot, resolver, log)));
  }

  return lines.join("\n");
};

export const handleScrubWorkspace = async (
  { workspaceRoot, log }: GitToolDeps,
  args: Static<typeof ScrubWorkspaceParams>,
): Promise<string> => {
  const project = args.project;

  if (project === "") {
    throw new Error("'project' cannot be empty; omit it to scrub the workspace repository.");
  }

  const isProject = project != null;
  const target = isProject ? join(workspaceRoot, "projects", project) : workspaceRoot;

  if (isProject && !(await exists(target))) {
    throw new Error(`Project '${project}' not found under projects/`);
  }

  const outcome = await scrubPaths(target, args.paths, log);

  // Non-project scrubs, or any outcome that didn't fully rewrite history, need
  // no further annotation.
  if (!isProject || outcome.code !== SCRUB_RESULT.scrubbed) {
    return outcome.message;
  }

  // After filter-repo rewrites a project's history, its commit SHAs change, so
  // the parent workspace's recorded submodule pointer goes stale. The next
  // session-close commit picks up the new pointer — surface that so the agent
  // isn't confused by the resulting "modified submodule" state.
  return `${outcome.message} The projects/${project} submodule pointer in the workspace will update at the next session-close commit.`;
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
        "Stage and commit all pending workspace changes in a single commit, then push the workspace and any project submodules to their origin remotes (pushing any commits already ahead of the remote, even when the working tree is clean). A descriptive message is generated from the changes unless one is provided. Pass push=false to commit without pushing. Changes are also committed and pushed automatically at session end — use this only when an immediate commit or push matters.",
      promptSnippet: "Commit and push workspace (and project) changes on demand",
      promptGuidelines: [
        "Use commit_workspace when the user explicitly asks to save, commit, or push (workspace or project) changes now rather than waiting for session end.",
      ],
      parameters: CommitWorkspaceParams,
      async execute(_toolCallId, params) {
        return textResult(await handleCommitWorkspace(deps, params));
      },
    });

    pi.registerTool({
      name: "scrub",
      label: "Scrub Git History",
      description:
        "Permanently remove the given paths from the ENTIRE git history of the workspace (or a registered project under projects/) via `git filter-repo`, then force-push to origin. Pass `project` to target a project submodule's history; omit it to scrub the workspace repo. This is DESTRUCTIVE and IRREVERSIBLE: it rewrites every commit that touched those paths and rewrites remote history. Requires a clean working tree and that `git filter-repo` is installed. Use only when the user explicitly wants files purged from history (e.g. a leaked secret or a large blob).",
      promptSnippet: "Purge paths from workspace or project git history",
      promptGuidelines: [
        "Use scrub only when the user explicitly asks to permanently erase files from git history; confirm the exact paths first since the rewrite is irreversible.",
        "When scrubbing a project, confirm the exact project name via list_projects first.",
      ],
      parameters: ScrubWorkspaceParams,
      async execute(_toolCallId, params) {
        return textResult(await handleScrubWorkspace(deps, params));
      },
    });
  };

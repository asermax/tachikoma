import { rm } from "node:fs/promises";
import { join } from "node:path";

import { runGit, runGitCapture } from "../../git/git.ts";
import { resolveRemoteDefaultBranch } from "../../git/remote.ts";
import { DIVERGENCE_STATUS, detectDivergence } from "../../git/sync.ts";

// `listSubmodules` is a core git primitive (it just parses `git submodule status`),
// so it lives in `src/git/git.ts` and is re-exported here for the project tools that
// already import it from this module.
export { listSubmodules } from "../../git/git.ts";

export const initSubmodule = async (workspaceRoot: string, path: string): Promise<void> => {
  await runGit(workspaceRoot, ["submodule", "update", "--init", path]);
};

/**
 * Resolve the default branch through the shared core resolver (local symbolic ref, then
 * `ls-remote --symref`), defaulting to "main" when both fail — project tooling is
 * best-effort and must keep working against an unreachable or empty remote, unlike
 * skill-evolution's strict must-abort resolution.
 */
export const resolveDefaultBranch = async (repoPath: string): Promise<string> => {
  try {
    return await resolveRemoteDefaultBranch(repoPath);
  } catch {
    return "main";
  }
};

export const checkoutBranch = async (repoPath: string, branch: string): Promise<void> => {
  await runGit(repoPath, ["checkout", branch]);
};

/** Porcelain status output, or null when the working tree is clean. */
export const uncommittedChangesDetail = async (repoPath: string): Promise<string | null> => {
  const { stdout } = await runGitCapture(repoPath, ["status", "--porcelain"]);
  return stdout === "" ? null : stdout;
};

export const isDirty = async (repoPath: string): Promise<boolean> =>
  (await uncommittedChangesDetail(repoPath)) != null;

export const addSubmodule = async (
  workspaceRoot: string,
  name: string,
  url: string,
): Promise<void> => {
  await runGit(workspaceRoot, ["submodule", "add", url, `projects/${name}`]);
};

/** Remove a submodule completely: deinit, git rm, and drop the .git/modules clone. */
export const removeSubmodule = async (workspaceRoot: string, name: string): Promise<void> => {
  const path = `projects/${name}`;

  await runGit(workspaceRoot, ["submodule", "deinit", "-f", path]);
  await runGit(workspaceRoot, ["rm", "-f", path]);
  await rm(join(workspaceRoot, ".git", "modules", "projects", name), {
    recursive: true,
    force: true,
  });
};

/** Current branch name, or null when HEAD is detached. */
export const currentBranch = async (repoPath: string): Promise<string | null> => {
  const result = await runGitCapture(repoPath, ["symbolic-ref", "--short", "HEAD"]);

  if (result.code !== 0 || result.stdout === "") return null;

  return result.stdout;
};

/**
 * Whether HEAD has commits not on the configured `origin` branch — a cheap,
 * fetch-free check against the last-known remote-tracking ref. Returns false for
 * detached HEAD or when no `origin/<branch>` ref exists (no remote / never
 * fetched), so callers only push submodules that genuinely look ahead.
 */
export const isAhead = async (repoPath: string): Promise<boolean> => {
  const branch = await currentBranch(repoPath);

  if (branch == null) return false;

  return (await detectDivergence(repoPath, "origin", branch)) === DIVERGENCE_STATUS.ahead;
};

export const currentCommitShort = async (repoPath: string): Promise<string> => {
  const result = await runGitCapture(repoPath, ["rev-parse", "--short", "HEAD"]);

  if (result.code !== 0 || result.stdout === "") return "unknown";

  return result.stdout;
};

export interface ProjectState {
  name: string;
  /** Branch name, or null when HEAD is detached. */
  branch: string | null;
  commit: string;
  dirtyFiles: number;
}

/** Gather display state (branch/commit + dirty file count) for one submodule path. */
export const projectState = async (workspaceRoot: string, path: string): Promise<ProjectState> => {
  const repoPath = join(workspaceRoot, path);
  const detail = await uncommittedChangesDetail(repoPath);

  return {
    name: path.split("/").at(-1) ?? path,
    branch: await currentBranch(repoPath),
    commit: await currentCommitShort(repoPath),
    dirtyFiles: detail == null ? 0 : detail.split("\n").length,
  };
};

export const describeProjectState = (state: ProjectState): string => {
  const location = state.branch ?? `${state.commit} (detached)`;
  const dirty =
    state.dirtyFiles > 0
      ? ` — ${state.dirtyFiles} uncommitted change${state.dirtyFiles === 1 ? "" : "s"}`
      : "";

  return `- ${state.name}: ${location}${dirty}`;
};

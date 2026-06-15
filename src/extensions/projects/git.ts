import { rm } from "node:fs/promises";
import { join } from "node:path";

import { runGit, runGitCapture } from "../../git/git.ts";

// `listSubmodules` is a core git primitive (it just parses `git submodule status`),
// so it lives in `src/git/git.ts` and is re-exported here for the project tools that
// already import it from this module.
export { listSubmodules } from "../../git/git.ts";

export const initSubmodule = async (workspaceRoot: string, path: string): Promise<void> => {
  await runGit(workspaceRoot, ["submodule", "update", "--init", path]);
};

/**
 * Resolve the default branch from the remote's HEAD reference: local symbolic
 * ref first, `git remote show origin` as a fallback, "main" as a last resort.
 */
export const resolveDefaultBranch = async (repoPath: string): Promise<string> => {
  const symbolic = await runGitCapture(repoPath, ["symbolic-ref", "refs/remotes/origin/HEAD"]);
  const prefix = "refs/remotes/origin/";

  if (symbolic.code === 0 && symbolic.stdout.startsWith(prefix)) {
    return symbolic.stdout.slice(prefix.length);
  }

  const show = await runGitCapture(repoPath, ["remote", "show", "origin"]);

  if (show.code === 0) {
    const headLine = show.stdout.split("\n").find((line) => line.includes("HEAD branch:"));

    if (headLine != null) return (headLine.split(":").at(-1) ?? "").trim() || "main";
  }

  return "main";
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

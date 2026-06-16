import type { Logger } from "../log.ts";
import type { CommitAgent } from "./commit-agent.ts";
import { commitSubjects, hasUncommittedChanges, runGit, runGitCapture } from "./git.ts";

export interface CommitAllOptions {
  /**
   * Drives the grouped-commit agent. Runs first whenever there are changes; the
   * `fallbackMessage` is used only if the agent fails or leaves the tree dirty.
   */
  agent: CommitAgent;
  cwd: string;
  /**
   * Deterministic message used for a single fallback commit when the agent
   * fails or leaves changes uncommitted.
   */
  fallbackMessage: string;
  log: Logger;
}

/** Subjects of every commit made since `head`, oldest-first. `null` head = all commits. */
const subjectsSince = (cwd: string, head: string | null): Promise<string[]> =>
  commitSubjects(cwd, head != null ? `${head}..HEAD` : "HEAD");

/**
 * Commit every change in `cwd`. The agent runs first, grouping changes into
 * cohesive commits; if it throws, returns nothing usable, or leaves the tree
 * dirty, whatever remains is committed in one fallback commit with
 * `fallbackMessage`. Returns the subjects of every commit made — read from git,
 * never the agent's report — or an empty array when there was nothing to commit.
 */
export const commitAll = async ({
  agent,
  cwd,
  fallbackMessage,
  log,
}: CommitAllOptions): Promise<string[]> => {
  if (!(await hasUncommittedChanges(cwd))) return [];

  const headResult = await runGitCapture(cwd, ["rev-parse", "HEAD"]);
  const head = headResult.code === 0 && headResult.stdout !== "" ? headResult.stdout : null;

  try {
    await agent(cwd, log);
    if (!(await hasUncommittedChanges(cwd))) return subjectsSince(cwd, head);
    log.warn({ path: cwd }, "commit agent left the tree dirty — committing the remainder");
  } catch (error) {
    log.warn({ err: error }, "commit agent failed — falling back to a single commit");
  }

  // Fallback: one commit with the deterministic message for whatever remains.
  await runGit(cwd, ["add", "-A"]);

  const { stdout: diffStat } = await runGitCapture(cwd, ["diff", "--cached", "--stat"]);

  if (diffStat === "") return subjectsSince(cwd, head);

  await runGit(cwd, ["commit", "-m", fallbackMessage]);

  return subjectsSince(cwd, head);
};

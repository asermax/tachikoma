import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { vi } from "vitest";

import type { CommitAgent } from "../../src/git/commit-agent.ts";
import { commitSubjects, runGit } from "../../src/git/git.ts";
import type { RebaseResolver } from "../../src/git/sync.ts";
import type { Logger } from "../../src/log.ts";
import type { DebouncedTask } from "../../src/util/debouncer.ts";

export const fakeLogger = (): Logger =>
  ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) as unknown as Logger;

/**
 * A `DebouncedTask` stand-in whose methods are `vi.fn()` mocks — `touch()`,
 * `clear()`, and `whenIdle()` (resolves immediately). Used to assert signal and
 * drain behavior without arming a real debounce timer.
 */
export const recordingDebouncer = (): DebouncedTask =>
  ({
    touch: vi.fn(),
    clear: vi.fn(),
    whenIdle: vi.fn().mockResolvedValue(undefined),
  }) as unknown as DebouncedTask;

export const makeTempDir = (): Promise<string> => mkdtemp(join(tmpdir(), "tachi-git-"));

/** Repo-local identity (and no signing) so commits work in any environment. */
export const configureIdentity = async (repo: string): Promise<void> => {
  await runGit(repo, ["config", "user.name", "Test User"]);
  await runGit(repo, ["config", "user.email", "test@local"]);
  await runGit(repo, ["config", "commit.gpgsign", "false"]);
};

export const initRepo = async (dir: string): Promise<void> => {
  await runGit(dir, ["init", "-b", "main"]);
  await configureIdentity(dir);
};

export const commitFile = async (
  repo: string,
  file: string,
  content: string,
  message: string,
): Promise<void> => {
  await writeFile(join(repo, file), content, "utf8");
  await runGit(repo, ["add", file]);
  await runGit(repo, ["commit", "-m", message]);
};

export interface RemotePair {
  origin: string;
  cloneA: string;
  cloneB: string;
}

/**
 * A bare origin seeded with one commit (via cloneA) plus a second clone, so
 * tests can produce ahead/behind/diverged topologies between the clones.
 */
export const setupRemotePair = async (base: string): Promise<RemotePair> => {
  const origin = join(base, "origin.git");
  await mkdir(origin);
  await runGit(origin, ["init", "--bare", "-b", "main"]);

  await runGit(base, ["clone", origin, "clone-a"]);
  const cloneA = join(base, "clone-a");
  await configureIdentity(cloneA);
  await commitFile(cloneA, "base.txt", "base\n", "Base commit");
  await runGit(cloneA, ["push", "-u", "origin", "main"]);

  await runGit(base, ["clone", origin, "clone-b"]);
  const cloneB = join(base, "clone-b");
  await configureIdentity(cloneB);

  return { origin, cloneA, cloneB };
};

export const headOf = (repo: string): Promise<string> => runGit(repo, ["rev-parse", "HEAD"]);

export const lastSubject = (repo: string): Promise<string> =>
  runGit(repo, ["log", "-1", "--format=%s"]);

/** Subjects of every commit on the repo, oldest-first. */
export const subjects = (repo: string): Promise<string[]> => commitSubjects(repo);

// ---- fake CommitAgent helpers ----------------------------------------------

/** A CommitAgent that stages everything and commits it once with `subject`. */
export const agentCommittingAs =
  (subject: string): CommitAgent =>
  async (cwd) => {
    await runGit(cwd, ["add", "-A"]);
    await runGit(cwd, ["commit", "-m", subject]);
  };

/**
 * A CommitAgent that makes one commit per group, staging only that group's
 * paths — the multi-commit grouping shape the real agent produces.
 */
export const agentCommittingGroups =
  (groups: { paths: string[]; subject: string }[]): CommitAgent =>
  async (cwd) => {
    for (const group of groups) {
      await runGit(cwd, ["add", ...group.paths]);
      await runGit(cwd, ["commit", "-m", group.subject]);
    }
  };

/** A CommitAgent that records it was called, then commits everything once. */
export const recordingAgentCommittingAs = (
  subject: string,
): { agent: CommitAgent; calls: number } => {
  const state = { calls: 0 };
  return {
    agent: async (cwd) => {
      state.calls += 1;
      await runGit(cwd, ["add", "-A"]);
      await runGit(cwd, ["commit", "-m", subject]);
    },
    get calls() {
      return state.calls;
    },
  };
};

/** A CommitAgent that throws, simulating an agent failure. */
export const agentThatThrows =
  (error: Error = new Error("agent failed")): CommitAgent =>
  async () => {
    throw error;
  };

/**
 * Stand-in for the side agent: drives the in-progress rebase to completion the
 * way the real agent would — resolves every conflicted file to a merged body,
 * stages it, and continues — without spawning an LLM.
 */
export const resolvingResolver: RebaseResolver = async (cwd) => {
  while (true) {
    const conflicted = await runGit(cwd, ["diff", "--name-only", "--diff-filter=U"]);

    if (conflicted === "") return;

    for (const file of conflicted.split("\n")) {
      await writeFile(join(cwd, file), "merged by agent\n", "utf8");
      await runGit(cwd, ["add", file]);
    }

    await runGit(cwd, ["-c", "core.editor=true", "rebase", "--continue"]);
  }
};

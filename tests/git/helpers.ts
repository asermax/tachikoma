import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { vi } from "vitest";

import { runGit } from "../../src/git/git.ts";
import type { Logger } from "../../src/log.ts";

export const fakeLogger = (): Logger =>
  ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) as unknown as Logger;

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

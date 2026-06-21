import { mkdir, rm, utimes, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { isFilterRepoAvailable, SCRUB_RESULT, scrubPaths } from "../../src/extensions/git/scrub.ts";
import { runGit, runGitCapture } from "../../src/git/git.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir, setupRemotePair } from "./helpers.ts";

let base: string;
let repo: string;
const log = fakeLogger();

const filterRepoInstalled = await isFilterRepoAvailable(process.cwd());

beforeEach(async () => {
  base = await makeTempDir();
  repo = join(base, "workspace");
  await mkdir(repo);
  await initRepo(repo);
  await commitFile(repo, "keep.txt", "keep\n", "Seed commit");
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("scrubPaths validation", () => {
  it("returns NO_PATHS for an empty path list", async () => {
    const outcome = await scrubPaths(repo, [], log);

    expect(outcome.code).toBe(SCRUB_RESULT.noPaths);
  });

  it("refuses to scrub a dirty working tree", async () => {
    await writeFile(join(repo, "dirty.txt"), "dirty\n", "utf8");

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.dirtyTree);
  });

  it("reports paths absent from history", async () => {
    const outcome = await scrubPaths(repo, ["never-existed.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.pathsNotFound);
    expect(outcome.missingPaths).toEqual(["never-existed.txt"]);
  });
});

describe.skipIf(!filterRepoInstalled)("scrubPaths rewrite", () => {
  it("purges a path from the entire history", async () => {
    await commitFile(repo, "secret.txt", "leaked\n", "Add secret");
    await commitFile(repo, "other.txt", "data\n", "Add other");

    const outcome = await scrubPaths(repo, ["secret.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.scrubbed);

    expect((await runGitCapture(repo, ["log", "--all", "--", "secret.txt"])).stdout).toBe("");
    expect((await runGitCapture(repo, ["log", "--all", "--", "other.txt"])).stdout).not.toBe("");
  });

  it("restores origin and force-pushes the rewritten history", async () => {
    const { origin, cloneA } = await setupRemotePair(base);

    await commitFile(cloneA, "secret.txt", "leaked\n", "Add secret");
    await runGit(cloneA, ["push", "origin", "main"]);

    const outcome = await scrubPaths(cloneA, ["secret.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.scrubbed);
    expect((await runGitCapture(cloneA, ["remote", "get-url", "origin"])).stdout).not.toBe("");

    const remoteLog = await runGit(origin, ["log", "--all", "--", "secret.txt"]);
    expect(remoteLog).toBe("");
  });

  it("stays non-interactive on a re-scrub with a stale already_ran marker", async () => {
    // filter-repo writes an `already_ran` marker after a run; if it's older than
    // a day, a subsequent run prompts on stdin and hangs (stdin is never attached
    // here). scrub clears the marker first, so a re-scrub stays non-interactive.
    await commitFile(repo, "secret.txt", "leaked\n", "Add secret");
    await commitFile(repo, "other.txt", "data\n", "Add other");

    const first = await scrubPaths(repo, ["secret.txt"], log);

    expect(first.code).toBe(SCRUB_RESULT.scrubbed);

    // Backdate the marker filter-repo wrote, past its 1-day continuation threshold.
    const gitDir = (await runGitCapture(repo, ["rev-parse", "--absolute-git-dir"])).stdout;
    const marker = join(gitDir, "filter-repo", "already_ran");
    const stale = (Date.now() - 2 * 86_400_000) / 1000;

    await utimes(marker, stale, stale);

    const second = await scrubPaths(repo, ["other.txt"], log);

    expect(second.code).toBe(SCRUB_RESULT.scrubbed);
    expect((await runGitCapture(repo, ["log", "--all", "--", "other.txt"])).stdout).toBe("");
  }, 15000);
});

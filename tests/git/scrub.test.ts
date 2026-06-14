import { mkdir, rm, writeFile } from "node:fs/promises";
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
});

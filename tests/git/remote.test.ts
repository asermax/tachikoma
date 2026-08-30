import { rm } from "node:fs/promises";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runGit, runGitCapture } from "../../src/git/git.ts";
import {
  fetchRemote,
  listRemoteBranchTips,
  resolveRemoteDefaultBranch,
} from "../../src/git/remote.ts";
import { commitFile, initRepo, makeTempDir, setupRemotePair } from "./helpers.ts";

let base: string;

beforeEach(async () => {
  base = await makeTempDir();
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("fetchRemote", () => {
  it("returns exit code 0 and updates tracking refs on success", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneB, "next.txt", "next\n", "Advance main");
    await runGit(cloneB, ["push"]);

    const before = await runGit(cloneA, ["rev-parse", "refs/remotes/origin/main"]);
    const result = await fetchRemote(cloneA);
    const after = await runGit(cloneA, ["rev-parse", "refs/remotes/origin/main"]);

    expect(result.code).toBe(0);
    expect(before).not.toBe(after);
    expect(after).toBe(await runGit(cloneB, ["rev-parse", "HEAD"]));
  });

  it("prunes tracking refs whose branches were deleted on the remote", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await runGit(cloneB, ["checkout", "-b", "topic"]);
    await commitFile(cloneB, "topic.txt", "topic\n", "Topic work");
    await runGit(cloneB, ["push", "-u", "origin", "topic"]);
    await fetchRemote(cloneA);
    const tracked = await runGit(cloneA, ["rev-parse", "--verify", "refs/remotes/origin/topic"]);
    expect(tracked).toMatch(/^[0-9a-f]{40}$/);

    await runGit(cloneB, ["push", "origin", "--delete", "topic"]);
    const result = await fetchRemote(cloneA);
    const pruned = await runGitCapture(cloneA, [
      "rev-parse",
      "--verify",
      "refs/remotes/origin/topic",
    ]);

    expect(result.code).toBe(0);
    expect(pruned.code).not.toBe(0);
  });

  it("surfaces the exit code when the fetch fails", async () => {
    await initRepo(base);
    const repo = base;
    await runGit(repo, ["remote", "add", "origin", "./no-such-remote.git"]);

    const result = await fetchRemote(repo);

    expect(result.code).not.toBe(0);
  });
});

describe("resolveRemoteDefaultBranch", () => {
  it("resolves through the local origin/HEAD symbolic ref when present", async () => {
    const { cloneB } = await setupRemotePair(base);
    const local = await runGitCapture(cloneB, ["symbolic-ref", "refs/remotes/origin/HEAD"]);

    expect(local.code).toBe(0);
    await expect(resolveRemoteDefaultBranch(cloneB)).resolves.toBe("main");
  });

  it("falls back to ls-remote --symref when the local symbolic ref is absent", async () => {
    const { cloneA } = await setupRemotePair(base);
    await runGitCapture(cloneA, ["update-ref", "-d", "refs/remotes/origin/HEAD"]);

    const local = await runGitCapture(cloneA, ["symbolic-ref", "refs/remotes/origin/HEAD"]);
    expect(local.code).not.toBe(0); // the offline path is unavailable…

    await expect(resolveRemoteDefaultBranch(cloneA)).resolves.toBe("main"); // …so this came online
  });

  it("throws when both the symbolic ref and the remote are unresolvable", async () => {
    await initRepo(base);

    await expect(resolveRemoteDefaultBranch(base)).rejects.toThrow(/could not resolve/);
  });
});

describe("listRemoteBranchTips", () => {
  it("lists only the branches matching the pattern, with refs/heads/ stripped", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await runGit(cloneB, ["checkout", "-b", "feature-x"]);
    await commitFile(cloneB, "x.txt", "x\n", "Feature x");
    await runGit(cloneB, ["push", "-u", "origin", "feature-x"]);
    await runGit(cloneB, ["checkout", "-b", "feature-y"]);
    await commitFile(cloneB, "y.txt", "y\n", "Feature y");
    await runGit(cloneB, ["push", "-u", "origin", "feature-y"]);

    const tips = await listRemoteBranchTips(cloneA, "feature-*");

    expect(new Set(tips.keys())).toEqual(new Set(["feature-x", "feature-y"]));
    expect(tips.get("feature-x")).toBe(await runGit(cloneB, ["rev-parse", "feature-x"]));
    expect(tips.get("feature-y")).toBe(await runGit(cloneB, ["rev-parse", "feature-y"]));
  });

  it("returns an empty map when nothing matches", async () => {
    const { cloneA } = await setupRemotePair(base);

    await expect(listRemoteBranchTips(cloneA, "skill-evolution/*")).resolves.toEqual(new Map());
  });

  it("throws when the listing itself fails", async () => {
    await initRepo(base);
    await runGit(base, ["remote", "add", "origin", "./no-such-remote.git"]);

    await expect(listRemoteBranchTips(base, "*")).rejects.toThrow(/ls-remote/);
  });
});

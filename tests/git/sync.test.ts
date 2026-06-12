import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runGit } from "../../src/extensions/git/git.ts";
import { PUSH_RESULT, SYNC_RESULT, smartPull, smartPush } from "../../src/extensions/git/sync.ts";
import {
  commitFile,
  fakeLogger,
  headOf,
  lastSubject,
  makeTempDir,
  setupRemotePair,
} from "./helpers.ts";

const log = fakeLogger();

let base: string;

beforeEach(async () => {
  base = await makeTempDir();
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("smartPull", () => {
  it("returns UP_TO_DATE when local matches the remote", async () => {
    const { cloneB } = await setupRemotePair(base);

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.upToDate);
  });

  it("fast-forwards when the local branch is behind", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "new.txt", "new\n", "Add new file");
    await runGit(cloneA, ["push", "origin", "main"]);

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.fastForwarded);
    expect(await headOf(cloneB)).toBe(await headOf(cloneA));
  });

  it("rebases local commits on top of the remote when diverged without conflicts", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "from-a.txt", "a\n", "Commit from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "from-b.txt", "b\n", "Commit from B");

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.rebaseSucceeded);
    expect(await lastSubject(cloneB)).toBe("Commit from B");
    expect(await runGit(cloneB, ["log", "--format=%s"])).toContain("Commit from A");
  });

  it("aborts the rebase and fails when the divergence conflicts", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "conflict.txt", "from A\n", "Conflicting from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "conflict.txt", "from B\n", "Conflicting from B");

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.syncFailed);

    // Local state restored: the local commit is intact and no rebase is pending.
    expect(await lastSubject(cloneB)).toBe("Conflicting from B");
    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
  });

  it("skips the sync entirely when the working tree is dirty", async () => {
    const { cloneB } = await setupRemotePair(base);
    await writeFile(join(cloneB, "dirty.txt"), "dirty\n", "utf8");

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.dirtySkipped);
  });
});

describe("smartPush", () => {
  it("returns NOTHING_TO_PUSH when local matches the remote", async () => {
    const { cloneB } = await setupRemotePair(base);

    expect(await smartPush(cloneB, "origin", "HEAD", log)).toBe(PUSH_RESULT.nothingToPush);
  });

  it("pushes directly when the local branch is ahead", async () => {
    const { origin, cloneB } = await setupRemotePair(base);
    await commitFile(cloneB, "ahead.txt", "ahead\n", "Ahead commit");

    expect(await smartPush(cloneB, "origin", "HEAD", log)).toBe(PUSH_RESULT.pushed);
    expect(await headOf(origin)).toBe(await headOf(cloneB));
  });

  it("rebases then pushes when diverged without conflicts", async () => {
    const { origin, cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "from-a.txt", "a\n", "Commit from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "from-b.txt", "b\n", "Commit from B");

    expect(await smartPush(cloneB, "origin", "HEAD", log)).toBe(PUSH_RESULT.rebaseSucceeded);
    expect(await headOf(origin)).toBe(await headOf(cloneB));
  });

  it("preserves local commits and fails when the divergence conflicts", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "conflict.txt", "from A\n", "Conflicting from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "conflict.txt", "from B\n", "Conflicting from B");

    expect(await smartPush(cloneB, "origin", "HEAD", log)).toBe(PUSH_RESULT.rebaseFailed);
    expect(await lastSubject(cloneB)).toBe("Conflicting from B");
    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
  });
});

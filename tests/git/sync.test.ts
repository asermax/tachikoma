import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { runGit, runGitCapture } from "../../src/git/git.ts";
import {
  PUSH_RESULT,
  PUSH_SUCCESS,
  type RebaseResolver,
  SYNC_RESULT,
  smartPull,
  smartPush,
} from "../../src/git/sync.ts";
import {
  commitFile,
  configureIdentity,
  fakeLogger,
  headOf,
  lastSubject,
  makeTempDir,
  setupRemotePair,
} from "./helpers.ts";

const log = fakeLogger();

/**
 * Stand-in for the side agent: drives the in-progress rebase to completion the
 * way the real agent would — resolves every conflicted file to a merged body,
 * stages it, and continues — without spawning an LLM.
 */
const resolvingResolver: RebaseResolver = async (cwd) => {
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

/** A side agent that cannot resolve the conflict — leaves the rebase untouched. */
const failingResolver: RebaseResolver = vi.fn(async () => {});

let base: string;

beforeEach(async () => {
  base = await makeTempDir();

  vi.mocked(log.debug).mockClear();
  vi.mocked(log.info).mockClear();
  vi.mocked(log.warn).mockClear();
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

describe("agent conflict resolution", () => {
  const seedConflict = async (): Promise<{ origin: string; cloneB: string }> => {
    const { origin, cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "conflict.txt", "from A\n", "Conflicting from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "conflict.txt", "from B\n", "Conflicting from B");

    return { origin, cloneB };
  };

  it("continues the pull and reports AGENT_RESOLVED when the agent resolves the conflict", async () => {
    const { cloneB } = await seedConflict();

    expect(await smartPull(cloneB, "origin", "HEAD", log, resolvingResolver)).toBe(
      SYNC_RESULT.agentResolved,
    );

    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
    expect(await runGit(cloneB, ["log", "--format=%s"])).toContain("Conflicting from A");
  });

  it("pushes after the agent resolves and reports AGENT_RESOLVED on push", async () => {
    const { origin, cloneB } = await seedConflict();

    expect(await smartPush(cloneB, "origin", "HEAD", log, resolvingResolver)).toBe(
      PUSH_RESULT.agentResolved,
    );
    expect(await headOf(origin)).toBe(await headOf(cloneB));
  });

  it("aborts to a clean tree and reports SYNC_FAILED when the agent cannot resolve", async () => {
    const { cloneB } = await seedConflict();

    expect(await smartPull(cloneB, "origin", "HEAD", log, failingResolver)).toBe(
      SYNC_RESULT.syncFailed,
    );

    // The resolver was given the chance, then the rebase was aborted to a clean state.
    expect(failingResolver).toHaveBeenCalled();
    expect(await lastSubject(cloneB)).toBe("Conflicting from B");
    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
  });

  it("still aborts and reports REBASE_FAILED on push when the agent cannot resolve", async () => {
    const { cloneB } = await seedConflict();

    expect(await smartPush(cloneB, "origin", "HEAD", log, failingResolver)).toBe(
      PUSH_RESULT.rebaseFailed,
    );
    expect(await lastSubject(cloneB)).toBe("Conflicting from B");
    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
  });

  it("reports SYNC_FAILED when the agent aborts the rebase as unresolvable", async () => {
    const { cloneB } = await seedConflict();

    const abortingResolver: RebaseResolver = vi.fn(async (cwd) => {
      await runGit(cwd, ["rebase", "--abort"]);
    });

    expect(await smartPull(cloneB, "origin", "HEAD", log, abortingResolver)).toBe(
      SYNC_RESULT.syncFailed,
    );

    // Cleared rebase state alone must not be mistaken for success — the remote
    // is not an ancestor of HEAD, so the local commit is intact.
    expect(abortingResolver).toHaveBeenCalledTimes(1);
    expect(await lastSubject(cloneB)).toBe("Conflicting from B");
    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
  });

  it("stops after the bounded attempts when the agent keeps leaving conflicts", async () => {
    const { cloneB } = await seedConflict();

    const stubbornResolver: RebaseResolver = vi.fn(async () => {});

    expect(await smartPull(cloneB, "origin", "HEAD", log, stubbornResolver)).toBe(
      SYNC_RESULT.syncFailed,
    );

    // Bounded by MAX_RESOLVER_ATTEMPTS (3) — never loops forever.
    expect(stubbornResolver).toHaveBeenCalledTimes(3);
    expect(await runGit(cloneB, ["status", "--porcelain"])).toBe("");
  });
});

describe("smartPull edge cases", () => {
  it("treats an ahead local branch as already up to date", async () => {
    const { cloneB } = await setupRemotePair(base);
    await commitFile(cloneB, "ahead.txt", "ahead\n", "Ahead commit");

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.upToDate);
  });

  it("returns SYNC_FAILED when an unexpected git error is thrown", async () => {
    const { cloneB } = await setupRemotePair(base);

    expect(await smartPull(cloneB, "no-such-remote", "HEAD", log)).toBe(SYNC_RESULT.syncFailed);
    expect(log.warn).toHaveBeenCalled();
  });

  it("resolves an explicit (non-HEAD) branch name through to the rebase ref", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "new.txt", "new\n", "Add new file");
    await runGit(cloneA, ["push", "origin", "main"]);

    expect(await smartPull(cloneB, "origin", "main", log)).toBe(SYNC_RESULT.fastForwarded);
  });

  it("falls back to the literal HEAD ref when HEAD is detached", async () => {
    const { cloneB } = await setupRemotePair(base);
    // Detached HEAD has no symbolic-ref, so resolveBranch keeps "HEAD";
    // origin/HEAD exists for a clone, so divergence still classifies cleanly.
    await runGit(cloneB, ["checkout", "--detach", "HEAD"]);

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.upToDate);
  });
});

describe("smartPush edge cases", () => {
  it("returns PUSH_FAILED when the rebase succeeds but the push is rejected", async () => {
    // A non-bare origin with main checked out refuses pushes to that branch, so
    // the rebase lands locally but the follow-up push fails.
    const origin = join(base, "origin");
    await runGit(base, ["init", "-b", "main", origin]);
    await configureIdentity(origin);
    await commitFile(origin, "base.txt", "base\n", "Base commit");
    await runGit(origin, ["config", "receive.denyCurrentBranch", "refuse"]);

    await runGit(base, ["clone", origin, "work"]);
    const work = join(base, "work");
    await configureIdentity(work);

    await commitFile(origin, "from-origin.txt", "o\n", "Origin commit");
    await commitFile(work, "from-work.txt", "w\n", "Work commit");

    expect(await smartPush(work, "origin", "HEAD", log)).toBe(PUSH_RESULT.pushFailed);
    expect(log.warn).toHaveBeenCalled();
  });

  it("returns REBASE_FAILED when an unexpected git error is thrown", async () => {
    const { cloneB } = await setupRemotePair(base);

    expect(await smartPush(cloneB, "no-such-remote", "HEAD", log)).toBe(PUSH_RESULT.rebaseFailed);
  });

  it("returns REBASE_FAILED when the rebase never starts", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    // Upstream introduces a tracked file; an untracked local file of the same
    // name would be clobbered, so git refuses to start the rebase at all.
    await commitFile(cloneA, "blocked.txt", "from A\n", "Add blocked from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "other.txt", "from B\n", "Commit from B");
    await writeFile(join(cloneB, "blocked.txt"), "untracked local\n", "utf8");

    expect(await smartPush(cloneB, "origin", "HEAD", log)).toBe(PUSH_RESULT.rebaseFailed);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: cloneB }),
      "rebase failed without starting (no conflicts)",
    );
  });
});

describe("stale rebase recovery", () => {
  it("aborts a genuine stale rebase cleanly before pushing", async () => {
    const { origin, cloneA, cloneB } = await setupRemotePair(base);
    // cloneB diverges from origin: a local commit plus a conflicting upstream one.
    await commitFile(cloneA, "shared.txt", "from A\n", "Shared from A");
    await runGit(cloneA, ["push", "origin", "main"]);
    await commitFile(cloneB, "shared.txt", "from B\n", "Shared from B");

    // Start a rebase that stops on the conflict, leaving real rebase state on
    // disk; the next smartPush (no dirty guard) must abort it cleanly first.
    await runGit(cloneB, ["fetch", "origin"]);
    await runGitCapture(cloneB, ["rebase", "origin/main"]);
    expect(await runGit(cloneB, ["rev-parse", "--git-path", "rebase-merge"])).toBeTruthy();

    const result = await smartPush(cloneB, "origin", "HEAD", log, resolvingResolver);

    expect(PUSH_SUCCESS.has(result)).toBe(true);
    expect(await headOf(origin)).toBe(await headOf(cloneB));
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: cloneB }),
      "stale rebase detected — aborting",
    );
    expect(log.warn).not.toHaveBeenCalledWith(expect.anything(), "failed to abort stale rebase");
  });

  it("warns when a leftover rebase dir cannot be aborted, then fails the sync", async () => {
    const { cloneA, cloneB } = await setupRemotePair(base);
    await commitFile(cloneA, "new.txt", "new\n", "Add new file");
    await runGit(cloneA, ["push", "origin", "main"]);

    // An empty rebase-merge dir trips the in-progress check, but `rebase --abort`
    // has nothing real to abort and fails — exercising the abort-failure path.
    await mkdir(join(cloneB, ".git", "rebase-merge"), { recursive: true });

    expect(await smartPull(cloneB, "origin", "HEAD", log)).toBe(SYNC_RESULT.syncFailed);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: cloneB }),
      "failed to abort stale rebase",
    );
  });

  it("resolves the real git dir through a gitlink file when detecting a stale rebase", async () => {
    const { origin } = await setupRemotePair(base);

    // `--separate-git-dir` makes .git a gitlink file (`gitdir: <path>`), as
    // worktrees and submodules do, so resolveGitDir must follow the pointer.
    const realGitDir = join(base, "real-git");
    await runGit(base, ["clone", "--separate-git-dir", realGitDir, origin, "linked"]);
    const linked = join(base, "linked");
    await configureIdentity(linked);

    await mkdir(join(realGitDir, "rebase-apply"), { recursive: true });

    await smartPull(linked, "origin", "HEAD", log);

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: linked }),
      "stale rebase detected — aborting",
    );
  });
});

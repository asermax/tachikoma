import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GitResult } from "../../src/git/git.ts";

const runGitCapture = vi.fn();

vi.mock("../../src/git/git.ts", () => ({
  runGit: vi.fn(),
  runGitCapture: (...args: unknown[]) => runGitCapture(...args),
}));

const {
  currentBranch,
  currentCommitShort,
  describeProjectState,
  isAhead,
  isDirty,
  projectState,
  resolveDefaultBranch,
  uncommittedChangesDetail,
} = await import("../../src/extensions/projects/git.ts");

const ok = (stdout: string): GitResult => ({ code: 0, stdout, stderr: "" });
const fail = (): GitResult => ({ code: 1, stdout: "", stderr: "boom" });

beforeEach(() => {
  runGitCapture.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("resolveDefaultBranch", () => {
  it("uses the local symbolic ref when present", async () => {
    runGitCapture.mockResolvedValueOnce(ok("origin/develop"));

    expect(await resolveDefaultBranch("/repo")).toBe("develop");
    expect(runGitCapture).toHaveBeenCalledTimes(1);
  });

  it("falls back to `ls-remote --symref` when the symbolic ref is missing", async () => {
    runGitCapture
      .mockResolvedValueOnce(fail())
      .mockResolvedValueOnce(ok("ref: refs/heads/trunk\tHEAD\nabc123\tHEAD"));

    expect(await resolveDefaultBranch("/repo")).toBe("trunk");
  });

  it("falls back when the symbolic ref output lacks the expected prefix", async () => {
    runGitCapture
      .mockResolvedValueOnce(ok("(unknown)"))
      .mockResolvedValueOnce(ok("ref: refs/heads/release\tHEAD"));

    expect(await resolveDefaultBranch("/repo")).toBe("release");
  });

  it("defaults to main when ls-remote has no symref line", async () => {
    runGitCapture.mockResolvedValueOnce(fail()).mockResolvedValueOnce(ok("abc123\tHEAD"));

    expect(await resolveDefaultBranch("/repo")).toBe("main");
  });

  it("defaults to main when the symref line names no branch", async () => {
    runGitCapture.mockResolvedValueOnce(fail()).mockResolvedValueOnce(ok("ref: refs/heads/"));

    expect(await resolveDefaultBranch("/repo")).toBe("main");
  });

  it("defaults to main when both lookups fail", async () => {
    runGitCapture.mockResolvedValueOnce(fail()).mockResolvedValueOnce(fail());

    expect(await resolveDefaultBranch("/repo")).toBe("main");
  });
});

describe("currentBranch", () => {
  it("returns the branch name when HEAD is on a branch", async () => {
    runGitCapture.mockResolvedValue(ok("feature"));

    expect(await currentBranch("/repo")).toBe("feature");
  });

  it("returns null when HEAD is detached", async () => {
    runGitCapture.mockResolvedValue(fail());

    expect(await currentBranch("/repo")).toBeNull();
  });

  it("returns null when the command yields empty output", async () => {
    runGitCapture.mockResolvedValue(ok(""));

    expect(await currentBranch("/repo")).toBeNull();
  });
});

describe("isAhead", () => {
  it("is true when the remote ref is an ancestor of HEAD but not vice-versa", async () => {
    // currentBranch, then detectDivergence: remote→HEAD ancestor (ok), HEAD→remote not (fail)
    runGitCapture
      .mockResolvedValueOnce(ok("main"))
      .mockResolvedValueOnce(ok(""))
      .mockResolvedValueOnce(fail());

    expect(await isAhead("/repo")).toBe(true);
  });

  it("is false when HEAD and the remote ref point at the same commit", async () => {
    runGitCapture
      .mockResolvedValueOnce(ok("main"))
      .mockResolvedValueOnce(ok(""))
      .mockResolvedValueOnce(ok(""));

    expect(await isAhead("/repo")).toBe(false);
  });

  it("is false when HEAD is behind the remote", async () => {
    runGitCapture
      .mockResolvedValueOnce(ok("main"))
      .mockResolvedValueOnce(fail())
      .mockResolvedValueOnce(ok(""));

    expect(await isAhead("/repo")).toBe(false);
  });

  it("is false on a detached HEAD without consulting divergence", async () => {
    runGitCapture.mockResolvedValueOnce(fail());

    expect(await isAhead("/repo")).toBe(false);
    expect(runGitCapture).toHaveBeenCalledTimes(1);
  });
});

describe("currentCommitShort", () => {
  it("returns the short hash", async () => {
    runGitCapture.mockResolvedValue(ok("abc1234"));

    expect(await currentCommitShort("/repo")).toBe("abc1234");
  });

  it("returns 'unknown' when rev-parse fails", async () => {
    runGitCapture.mockResolvedValue(fail());

    expect(await currentCommitShort("/repo")).toBe("unknown");
  });

  it("returns 'unknown' when rev-parse yields empty output", async () => {
    runGitCapture.mockResolvedValue(ok(""));

    expect(await currentCommitShort("/repo")).toBe("unknown");
  });
});

describe("projectState and describeProjectState", () => {
  it("counts dirty files and falls back to the path when there is no basename", async () => {
    runGitCapture
      .mockResolvedValueOnce(ok(" M a.txt\n M b.txt"))
      .mockResolvedValueOnce(ok("topic"))
      .mockResolvedValueOnce(ok("abc1234"));

    const state = await projectState("/ws", "projects/app");

    expect(state).toEqual({ name: "app", branch: "topic", commit: "abc1234", dirtyFiles: 2 });
  });

  it("reports zero dirty files for a clean tree", async () => {
    runGitCapture
      .mockResolvedValueOnce(ok(""))
      .mockResolvedValueOnce(ok("main"))
      .mockResolvedValueOnce(ok("abc1234"));

    const state = await projectState("/ws", "lib");

    expect(state.dirtyFiles).toBe(0);
    expect(state.name).toBe("lib");
  });

  it("renders a branch with a singular change suffix", () => {
    expect(
      describeProjectState({ name: "app", branch: "main", commit: "abc", dirtyFiles: 1 }),
    ).toBe("- app: main — 1 uncommitted change");
  });

  it("renders a detached commit with no changes", () => {
    expect(
      describeProjectState({ name: "app", branch: null, commit: "abc1234", dirtyFiles: 0 }),
    ).toBe("- app: abc1234 (detached)");
  });
});

describe("uncommittedChangesDetail and isDirty", () => {
  it("reports null and clean when the working tree is empty", async () => {
    runGitCapture.mockResolvedValue(ok(""));

    expect(await uncommittedChangesDetail("/repo")).toBeNull();
    expect(await isDirty("/repo")).toBe(false);
  });

  it("reports the porcelain detail and dirty when there are changes", async () => {
    runGitCapture.mockResolvedValue(ok(" M file.txt"));

    expect(await uncommittedChangesDetail("/repo")).toBe(" M file.txt");
    expect(await isDirty("/repo")).toBe(true);
  });
});

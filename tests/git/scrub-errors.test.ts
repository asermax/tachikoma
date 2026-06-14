import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeLogger } from "./helpers.ts";

vi.mock("node:child_process", () => ({ execFile: vi.fn() }));
vi.mock("../../src/git/git.ts", () => ({
  runGit: vi.fn(),
  runGitCapture: vi.fn(),
  hasUncommittedChanges: vi.fn(),
  hasRemote: vi.fn(),
}));

import { execFile } from "node:child_process";
import { isFilterRepoAvailable, SCRUB_RESULT, scrubPaths } from "../../src/extensions/git/scrub.ts";
import { hasRemote, hasUncommittedChanges, runGit, runGitCapture } from "../../src/git/git.ts";

const log = fakeLogger();
const repo = "/fake/repo";

const ok = (stdout: string) => ({ stdout, stderr: "", code: 0 });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(hasUncommittedChanges).mockResolvedValue(false);
  vi.mocked(hasRemote).mockResolvedValue(true);
  vi.mocked(runGit).mockResolvedValue("");
});

describe("isFilterRepoAvailable", () => {
  it("returns true when the version probe succeeds", async () => {
    vi.mocked(execFile).mockImplementation(((
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, out: { stdout: string; stderr: string }) => void,
    ) => {
      cb(null, { stdout: "git-filter-repo 2.38\n", stderr: "" });
      return undefined as never;
    }) as unknown as typeof execFile);

    expect(await isFilterRepoAvailable(repo)).toBe(true);
  });

  it("returns false when the version probe throws", async () => {
    vi.mocked(execFile).mockImplementation(((
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, out: { stdout: string; stderr: string }) => void,
    ) => {
      cb(new Error("command not found"), { stdout: "", stderr: "" });
      return undefined as never;
    }) as unknown as typeof execFile);

    expect(await isFilterRepoAvailable(repo)).toBe(false);
  });
});

describe("scrubPaths error branches", () => {
  const filterRepoAvailable = () =>
    vi.mocked(execFile).mockImplementation(((
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, out: { stdout: string; stderr: string }) => void,
    ) => {
      cb(null, { stdout: "git-filter-repo\n", stderr: "" });
      return undefined as never;
    }) as unknown as typeof execFile);

  it("reports FILTER_REPO_NOT_INSTALLED when the tool is missing", async () => {
    vi.mocked(execFile).mockImplementation(((
      _cmd: string,
      _args: string[],
      _opts: unknown,
      cb: (err: Error | null, out: { stdout: string; stderr: string }) => void,
    ) => {
      cb(new Error("not found"), { stdout: "", stderr: "" });
      return undefined as never;
    }) as unknown as typeof execFile);

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.notInstalled);
  });

  it("reports FAILED when git filter-repo exits non-zero", async () => {
    filterRepoAvailable();

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      if (args[0] === "filter-repo") return { stdout: "", stderr: "boom", code: 1 };

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.failed);
    expect(outcome.message).toContain("boom");
  });

  it("reports FAILED using the exit code when stderr is empty", async () => {
    filterRepoAvailable();

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      if (args[0] === "filter-repo") return { stdout: "", stderr: "", code: 2 };

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.failed);
    expect(outcome.message).toContain("exit code 2");
  });

  it("returns SCRUBBED with no push when there is no origin remote", async () => {
    filterRepoAvailable();

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      if (args[0] === "remote") return ok("");

      if (args[0] === "filter-repo") return ok("");

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.scrubbed);
    expect(outcome.message).toContain("No origin remote");
  });

  it("restores origin then force-pushes when the remote was stripped", async () => {
    filterRepoAvailable();
    vi.mocked(hasRemote).mockResolvedValue(false);

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      if (args[0] === "remote") return ok("https://example.com/repo.git");

      if (args[0] === "filter-repo") return ok("");

      if (args[0] === "push") return ok("");

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.scrubbed);
    expect(outcome.message).toContain("force-pushed to origin");
    expect(runGit).toHaveBeenCalledWith(repo, [
      "remote",
      "add",
      "origin",
      "https://example.com/repo.git",
    ]);
  });

  it("reports SCRUBBED_PUSH_FAILED when the force-push fails", async () => {
    filterRepoAvailable();

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      if (args[0] === "remote") return ok("https://example.com/repo.git");

      if (args[0] === "filter-repo") return ok("");

      if (args[0] === "push") return { stdout: "", stderr: "rejected", code: 1 };

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.scrubbedPushFailed);
    expect(outcome.message).toContain("rejected");
  });

  it("reports SCRUBBED_PUSH_FAILED using the exit code when stderr is empty", async () => {
    filterRepoAvailable();

    vi.mocked(runGitCapture).mockImplementation(async (_cwd, args) => {
      if (args[0] === "log") return ok("commit abc\n");

      if (args[0] === "remote") return ok("https://example.com/repo.git");

      if (args[0] === "filter-repo") return ok("");

      if (args[0] === "push") return { stdout: "", stderr: "", code: 3 };

      return ok("");
    });

    const outcome = await scrubPaths(repo, ["keep.txt"], log);

    expect(outcome.code).toBe(SCRUB_RESULT.scrubbedPushFailed);
    expect(outcome.message).toContain("exit code 3");
  });
});

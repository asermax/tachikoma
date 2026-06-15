import { execFile } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  hasRemote,
  hasUncommittedChanges,
  listSubmodules,
  runGit,
  runGitCapture,
} from "../../src/git/git.ts";
import { initRepo, makeTempDir } from "./helpers.ts";

vi.mock("node:child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:child_process")>();
  const wrapped = vi.fn(actual.execFile);

  // promisify(execFile) in the module under test resolves its implementation
  // once, via this symbol, at load time. Point it at a delegator that reads the
  // mock's *current* implementation on every call, so per-test overrides take
  // effect while the default still runs real git.
  const promisifyCustom = Symbol.for("nodejs.util.promisify.custom");
  (wrapped as unknown as Record<symbol, unknown>)[promisifyCustom] = (
    file: string,
    args: readonly string[],
    options: unknown,
  ) =>
    new Promise((resolvePromise, rejectPromise) => {
      wrapped(file, args, options, (err: unknown, stdout?: unknown, stderr?: unknown) => {
        if (err != null) {
          // The real execFile's promisify.custom attaches stdout/stderr onto the
          // rejected error; the callback form passes them separately, so mirror
          // that here for the module under test to read them back.
          Object.assign(err as object, { stdout, stderr });
          rejectPromise(err);
          return;
        }

        resolvePromise({ stdout, stderr });
      });
    });

  return { ...actual, execFile: wrapped };
});

const execFileMock = vi.mocked(execFile);
const realExecFile = execFileMock.getMockImplementation();

let base: string;

beforeEach(async () => {
  base = await makeTempDir();
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });

  if (realExecFile != null) execFileMock.mockImplementation(realExecFile);
});

describe("runGitCapture", () => {
  it("returns code 0 with trimmed output on success", async () => {
    await initRepo(base);

    const result = await runGitCapture(base, ["rev-parse", "--is-inside-work-tree"]);

    expect(result.code).toBe(0);
    expect(result.stdout).toBe("true");
    expect(result.stderr).toBe("");
  });

  it("captures a non-zero exit with a numeric code and stderr", async () => {
    await initRepo(base);
    await runGit(base, ["commit", "--allow-empty", "-m", "init"]);

    const result = await runGitCapture(base, ["checkout", "no-such-branch"]);

    expect(result.code).not.toBe(0);
    expect(typeof result.code).toBe("number");
    expect(result.stderr).not.toBe("");
  });

  it("defaults stdout/stderr to empty when the failure carries neither", async () => {
    // A spawn-level error (not a non-zero exit) rejects with a bare Error that
    // has no stdout/stderr fields, exercising the nullish-coalescing fallbacks.
    execFileMock.mockImplementation(((
      _file: string,
      _args: readonly string[],
      _options: unknown,
      callback?: (err: Error | null) => void,
    ) => {
      const cb = typeof _options === "function" ? _options : callback;
      (cb as (err: Error | null) => void)(new Error("spawn boom"));

      return {} as ReturnType<typeof execFile>;
    }) as unknown as typeof execFile);

    const result = await runGitCapture(base, ["status"]);

    expect(result.code).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.stderr).toBe("");
  });

  it("falls back to code 1 when the spawned process has no numeric exit code", async () => {
    // ENOENT-style failure: git is invoked in a directory that does not exist,
    // so the error carries a string `code` rather than a numeric exit status.
    const result = await runGitCapture(join(base, "missing"), ["status"]);

    expect(result.code).toBe(1);
    expect(result.stdout).toBe("");
  });
});

describe("runGit", () => {
  it("returns trimmed stdout on success", async () => {
    await initRepo(base);

    expect(await runGit(base, ["rev-parse", "--is-inside-work-tree"])).toBe("true");
  });

  it("throws with the stderr message on failure", async () => {
    await initRepo(base);

    await expect(runGit(base, ["rev-parse", "--verify", "nope"])).rejects.toThrow(/failed:/);
  });

  it("throws with the exit code when stderr is empty", async () => {
    await initRepo(base);

    // `git --version` exits 0, but a bad config invocation here exits non-zero
    // with no stderr text: assert the exit-code fallback in the message.
    await expect(runGit(base, ["check-ref-format", "bad ref name"])).rejects.toThrow(
      /exit code \d+/,
    );
  });
});

describe("hasUncommittedChanges", () => {
  it("returns false on a clean tree", async () => {
    await initRepo(base);
    await runGit(base, ["commit", "--allow-empty", "-m", "init"]);

    expect(await hasUncommittedChanges(base)).toBe(false);
  });

  it("returns true when the tree has changes", async () => {
    await initRepo(base);
    await runGit(base, ["commit", "--allow-empty", "-m", "init"]);
    await writeFile(join(base, "dirty.txt"), "dirty\n", "utf8");

    expect(await hasUncommittedChanges(base)).toBe(true);
  });
});

describe("hasRemote", () => {
  it("returns false when the remote is not configured", async () => {
    await initRepo(base);

    expect(await hasRemote(base, "origin")).toBe(false);
  });

  it("returns true when the remote exists", async () => {
    await initRepo(base);
    await runGit(base, ["remote", "add", "origin", "https://example.com/repo.git"]);

    expect(await hasRemote(base, "origin")).toBe(true);
  });
});

describe("listSubmodules", () => {
  it("returns an empty list for a repo with no submodules", async () => {
    await initRepo(base);
    await runGit(base, ["commit", "--allow-empty", "-m", "init"]);

    expect(await listSubmodules(base)).toEqual([]);
  });
});

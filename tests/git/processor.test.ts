import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { PostProcessorContext } from "../../src/extensions/api.ts";
import { createGitProcessor } from "../../src/extensions/git/processor.ts";
import type { Completer } from "../../src/git/commit.ts";
import { runGit } from "../../src/git/git.ts";
import { commitFile, fakeLogger, headOf, initRepo, lastSubject, makeTempDir } from "./helpers.ts";

const ctxLog = (): PostProcessorContext["log"] => fakeLogger();

const context = (log = ctxLog()): PostProcessorContext => ({
  session: {} as SessionRecord,
  transcriptPath: null,
  log,
});

const completerReturning = (message: string): Completer => ({
  complete: vi.fn().mockResolvedValue(message),
});

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, "seed.txt", "seed\n", "Seed commit");
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("git processor", () => {
  it("stages and commits all changes with the generated message", async () => {
    await writeFile(join(workspace, "notes.md"), "remember this\n", "utf8");
    const side = completerReturning("Add session notes");

    await createGitProcessor({ workspaceRoot: workspace, side }).process(context());

    expect(await lastSubject(workspace)).toBe("Add session notes");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
    expect(side.complete).toHaveBeenCalledWith(
      expect.objectContaining({ user: expect.stringContaining("notes.md") }),
    );
  });

  it("does nothing when the workspace is clean", async () => {
    const side = completerReturning("unused");
    const head = await headOf(workspace);

    await createGitProcessor({ workspaceRoot: workspace, side }).process(context());

    expect(side.complete).not.toHaveBeenCalled();
    expect(await headOf(workspace)).toBe(head);
  });

  it("falls back to a deterministic message when generation fails", async () => {
    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const side: Completer = { complete: vi.fn().mockRejectedValue(new Error("model down")) };

    await createGitProcessor({ workspaceRoot: workspace, side }).process(context());

    expect(await lastSubject(workspace)).toMatch(/^Update workspace files \(\d{4}-\d{2}-\d{2}\)$/);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("pushes to origin after committing when a remote is configured", async () => {
    const origin = join(base, "origin.git");
    await runGit(base, ["init", "--bare", "-b", "main", origin]);
    await runGit(workspace, ["remote", "add", "origin", origin]);
    await runGit(workspace, ["push", "-u", "origin", "main"]);

    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Add notes"),
    }).process(context());

    expect(await headOf(origin)).toBe(await headOf(workspace));
  });

  it("warns and keeps changes local when the push cannot be reconciled", async () => {
    const origin = join(base, "origin.git");
    await runGit(base, ["init", "--bare", "-b", "main", origin]);
    await runGit(workspace, ["remote", "add", "origin", origin]);
    await runGit(workspace, ["push", "-u", "origin", "main"]);

    const other = join(base, "other");
    await runGit(base, ["clone", origin, "other"]);
    await runGit(other, ["config", "user.name", "Other"]);
    await runGit(other, ["config", "user.email", "other@local"]);
    await runGit(other, ["config", "commit.gpgsign", "false"]);
    await commitFile(other, "seed.txt", "remote edit\n", "Remote conflicting edit");
    await runGit(other, ["push", "origin", "main"]);

    await writeFile(join(workspace, "seed.txt"), "local edit\n", "utf8");
    const log = ctxLog();

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Local change"),
    }).process(context(log));

    expect(await lastSubject(workspace)).toBe("Local change");
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ result: "REBASE_FAILED" }),
      expect.stringContaining("push failed"),
    );
  });

  it("succeeds on retry when the second commit pass clears the tree", async () => {
    const hook = join(workspace, ".git", "hooks", "post-commit");
    const leftover = join(workspace, "leftover.txt");
    await writeFile(
      hook,
      `#!/bin/sh\nif [ ! -f "${leftover}" ]; then echo dirty > "${leftover}"; fi\n`,
      { mode: 0o755 },
    );

    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const log = ctxLog();

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Add notes"),
    }).process(context(log));

    expect(log.warn).toHaveBeenCalledWith(expect.stringContaining("retrying"));
    expect(log.warn).not.toHaveBeenCalledWith(
      expect.stringContaining("remain after git processor retry"),
    );
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("warns when changes still remain after the retry pass", async () => {
    const hook = join(workspace, ".git", "hooks", "post-commit");
    const stamp = join(workspace, "post-commit-stamp");
    await writeFile(hook, `#!/bin/sh\ndate +%s%N >> "${stamp}"\n`, { mode: 0o755 });

    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const log = ctxLog();

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Add notes"),
    }).process(context(log));

    expect(log.warn).toHaveBeenCalledWith(expect.stringContaining("retrying"));
    expect(log.warn).toHaveBeenCalledWith(
      expect.stringContaining("remain after git processor retry"),
    );
  });
});

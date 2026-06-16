import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { PostProcessorContext } from "../../src/extensions/api.ts";
import { createGitProcessor } from "../../src/extensions/git/processor.ts";
import type { CommitAgent } from "../../src/git/commit-agent.ts";
import { runGit } from "../../src/git/git.ts";
import {
  agentCommittingAs,
  agentThatThrows,
  commitFile,
  fakeLogger,
  headOf,
  initRepo,
  lastSubject,
  makeTempDir,
} from "./helpers.ts";

const ctxLog = (): PostProcessorContext["log"] => fakeLogger();

const context = (log = ctxLog()): PostProcessorContext => ({
  session: {} as SessionRecord,
  transcriptPath: null,
  log,
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
  it("commits all changes via the agent", async () => {
    await writeFile(join(workspace, "notes.md"), "remember this\n", "utf8");

    await createGitProcessor({
      workspaceRoot: workspace,
      agent: agentCommittingAs("Add session notes"),
    }).process(context());

    expect(await lastSubject(workspace)).toBe("Add session notes");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("does nothing and does not invoke the agent when the workspace is clean", async () => {
    const recorder = { calls: 0 };
    const agent: CommitAgent = async () => {
      recorder.calls += 1;
    };
    const head = await headOf(workspace);

    await createGitProcessor({ workspaceRoot: workspace, agent }).process(context());

    expect(recorder.calls).toBe(0);
    expect(await headOf(workspace)).toBe(head);
  });

  it("falls back to a deterministic message when the agent fails", async () => {
    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");

    await createGitProcessor({
      workspaceRoot: workspace,
      agent: agentThatThrows(new Error("model down")),
    }).process(context());

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
      agent: agentCommittingAs("Add notes"),
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
      agent: agentCommittingAs("Local change"),
    }).process(context(log));

    expect(await lastSubject(workspace)).toBe("Local change");
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ result: "REBASE_FAILED" }),
      expect.stringContaining("push failed"),
    );
  });

  it("does not retry when the commit pass leaves the tree clean", async () => {
    // A post-commit hook creates a file once; commitAll's fallback mops it up
    // within a single pass, so the processor never enters the retry branch.
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
      agent: agentCommittingAs("Add notes"),
    }).process(context(log));

    expect(log.warn).not.toHaveBeenCalledWith(expect.stringContaining("retrying"));
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("retries and warns when changes keep appearing after each commit pass", async () => {
    // A non-idempotent post-commit hook appends a fresh line on every commit, so
    // the tree is perpetually dirty — the processor retries once, then warns.
    const hook = join(workspace, ".git", "hooks", "post-commit");
    const stamp = join(workspace, "post-commit-stamp");
    await writeFile(hook, `#!/bin/sh\ndate +%s%N >> "${stamp}"\n`, { mode: 0o755 });

    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const log = ctxLog();

    await createGitProcessor({
      workspaceRoot: workspace,
      agent: agentCommittingAs("Add notes"),
    }).process(context(log));

    expect(log.warn).toHaveBeenCalledWith(expect.stringContaining("retrying"));
    expect(log.warn).toHaveBeenCalledWith(
      expect.stringContaining("remain after git processor retry"),
    );
  });
});

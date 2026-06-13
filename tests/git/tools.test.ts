import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { runGit } from "../../src/extensions/git/git.ts";
import {
  createGitToolsFactory,
  type GitToolDeps,
  handleCommitWorkspace,
  handleListRecentCommits,
  handleQueryGitStatus,
} from "../../src/extensions/git/tools.ts";
import { commitFile, fakeLogger, initRepo, lastSubject, makeTempDir } from "./helpers.ts";

let workspace: string;
let deps: GitToolDeps;

beforeEach(async () => {
  workspace = join(await makeTempDir(), "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, "seed.txt", "seed\n", "Seed commit");

  deps = {
    workspaceRoot: workspace,
    side: { complete: vi.fn().mockResolvedValue("Generated message") },
    log: fakeLogger(),
  };
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("handleQueryGitStatus", () => {
  it("reports a clean tree with the current branch", async () => {
    const output = await handleQueryGitStatus(deps);

    expect(output).toContain("On branch main");
    expect(output).toContain("Working tree is clean.");
  });

  it("lists uncommitted changes", async () => {
    await writeFile(join(workspace, "pending.md"), "draft\n", "utf8");

    const output = await handleQueryGitStatus(deps);

    expect(output).toContain("Uncommitted changes:");
    expect(output).toContain("pending.md");
  });
});

describe("handleListRecentCommits", () => {
  it("lists commits newest first, respecting the limit", async () => {
    await commitFile(workspace, "a.txt", "a\n", "Second commit");
    await commitFile(workspace, "b.txt", "b\n", "Third commit");

    const output = await handleListRecentCommits(deps, { limit: 2 });

    expect(output).toContain("Third commit");
    expect(output).toContain("Second commit");
    expect(output).not.toContain("Seed commit");
  });

  it("handles a repo without commits", async () => {
    const empty = join(workspace, "empty");
    await mkdir(empty);
    await initRepo(empty);

    const output = await handleListRecentCommits({ ...deps, workspaceRoot: empty }, {});

    expect(output).toBe("No commits found.");
  });
});

describe("handleCommitWorkspace", () => {
  it("commits with an explicit message without invoking generation", async () => {
    await writeFile(join(workspace, "pending.md"), "draft\n", "utf8");

    const output = await handleCommitWorkspace(deps, { message: "Save the draft" });

    expect(output).toContain("Save the draft");
    expect(await lastSubject(workspace)).toBe("Save the draft");
    expect(deps.side.complete).not.toHaveBeenCalled();
  });

  it("generates a message from the staged changes when none is given", async () => {
    await writeFile(join(workspace, "pending.md"), "draft\n", "utf8");

    await handleCommitWorkspace(deps, {});

    expect(await lastSubject(workspace)).toBe("Generated message");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("reports a clean tree without committing", async () => {
    const output = await handleCommitWorkspace(deps, {});

    expect(output).toBe("Nothing to commit — the working tree is clean.");
    expect(await lastSubject(workspace)).toBe("Seed commit");
  });
});

describe("createGitToolsFactory", () => {
  it("registers the workspace git tools", () => {
    const names: string[] = [];
    const pi = { registerTool: (tool: { name: string }) => names.push(tool.name) };

    createGitToolsFactory(deps)(pi as unknown as Parameters<ExtensionFactory>[0]);

    expect(names).toEqual(["query_git_status", "list_recent_commits", "commit_workspace", "scrub"]);
  });
});

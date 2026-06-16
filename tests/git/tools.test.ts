import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  createGitToolsFactory,
  type GitToolDeps,
  handleCommitWorkspace,
  handleListRecentCommits,
  handleQueryGitStatus,
  handleScrubWorkspace,
} from "../../src/extensions/git/tools.ts";
import { runGit } from "../../src/git/git.ts";
import {
  agentCommittingAs,
  agentThatThrows,
  commitFile,
  fakeLogger,
  initRepo,
  lastSubject,
  makeTempDir,
} from "./helpers.ts";

let workspace: string;
let deps: GitToolDeps;

beforeEach(async () => {
  workspace = join(await makeTempDir(), "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, "seed.txt", "seed\n", "Seed commit");

  deps = {
    workspaceRoot: workspace,
    agent: agentCommittingAs("Generated message"),
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

  it("reports a detached HEAD when symbolic-ref has no branch", async () => {
    await commitFile(workspace, "second.txt", "second\n", "Second commit");
    await runGit(workspace, ["checkout", "--detach", "HEAD"]);

    const output = await handleQueryGitStatus(deps);

    expect(output).toContain("Detached HEAD or unborn branch");
  });

  it("throws when git status fails outside a repository", async () => {
    const nonRepo = join(await makeTempDir(), "loose");
    await mkdir(nonRepo);

    await expect(handleQueryGitStatus({ ...deps, workspaceRoot: nonRepo })).rejects.toThrow(
      /git status failed/,
    );
  });
});

describe("handleScrubWorkspace", () => {
  it("returns the scrub outcome message for paths absent from history", async () => {
    const message = await handleScrubWorkspace(deps, { paths: ["never-existed.txt"] });

    expect(message).toMatch(/never-existed\.txt/);
  });

  it("throws when the named project does not exist", async () => {
    await expect(
      handleScrubWorkspace(deps, { paths: ["secret.txt"], project: "nope" }),
    ).rejects.toThrow(/Project 'nope' not found/);
  });

  it("rejects an empty project name rather than resolving to projects/", async () => {
    await expect(
      handleScrubWorkspace(deps, { paths: ["secret.txt"], project: "" }),
    ).rejects.toThrow(/'project' cannot be empty/);
  });

  it("targets the project repo, not the workspace, when a project is named", async () => {
    const projectRepo = join(workspace, "projects", "myproj");
    await mkdir(projectRepo, { recursive: true });
    await initRepo(projectRepo);
    await commitFile(projectRepo, "proj.txt", "proj\n", "Project seed");

    // `seed.txt` lives in the workspace history but not the project's; resolving
    // to the project repo must report it as absent from history (PATHS_NOT_FOUND),
    // which proves the project — not the workspace — was the scrub target.
    const message = await handleScrubWorkspace(deps, { paths: ["seed.txt"], project: "myproj" });

    expect(message).toMatch(/seed\.txt/);
    expect(message).toMatch(/not found in git history/);
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
  it("commits via the agent when no message is given", async () => {
    await writeFile(join(workspace, "pending.md"), "draft\n", "utf8");

    const output = await handleCommitWorkspace(deps, {});

    expect(output).toContain("Generated message");
    expect(await lastSubject(workspace)).toBe("Generated message");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("uses the message as a fallback when the agent fails", async () => {
    await writeFile(join(workspace, "pending.md"), "draft\n", "utf8");

    const output = await handleCommitWorkspace(
      { ...deps, agent: agentThatThrows() },
      { message: "Save the draft" },
    );

    expect(output).toContain("Save the draft");
    expect(await lastSubject(workspace)).toBe("Save the draft");
  });

  it("reports a clean tree without committing", async () => {
    const output = await handleCommitWorkspace(deps, {});

    expect(output).toBe("Nothing to commit — the working tree is clean.");
    expect(await lastSubject(workspace)).toBe("Seed commit");
  });
});

interface RegisteredTool {
  name: string;
  execute: (toolCallId: string, params: unknown) => Promise<{ content: { text: string }[] }>;
}

describe("createGitToolsFactory", () => {
  const register = (): RegisteredTool[] => {
    const tools: RegisteredTool[] = [];
    const pi = { registerTool: (tool: RegisteredTool) => tools.push(tool) };

    createGitToolsFactory(deps)(pi as unknown as Parameters<ExtensionFactory>[0]);

    return tools;
  };

  it("registers the workspace git tools", () => {
    expect(register().map((tool) => tool.name)).toEqual([
      "query_git_status",
      "list_recent_commits",
      "commit_workspace",
      "scrub",
    ]);
  });

  it("wires each tool's execute to the matching handler", async () => {
    const tools = register();
    const byName = (name: string) => tools.find((tool) => tool.name === name) as RegisteredTool;

    const status = await byName("query_git_status").execute("call-1", {});
    expect(status.content[0]?.text).toContain("On branch main");

    await commitFile(workspace, "later.txt", "later\n", "Later commit");
    const commits = await byName("list_recent_commits").execute("call-2", { limit: 1 });
    expect(commits.content[0]?.text).toContain("Later commit");

    await writeFile(join(workspace, "draft.md"), "draft\n", "utf8");
    const committed = await byName("commit_workspace").execute("call-3", {});
    expect(committed.content[0]?.text).toContain("Committed workspace changes");

    const scrubbed = await byName("scrub").execute("call-4", { paths: ["never-existed.txt"] });
    expect(scrubbed.content[0]?.text).toMatch(/never-existed\.txt/);
  });
});

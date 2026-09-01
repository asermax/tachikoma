import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HeadlessRunOptions } from "../../src/agent/side-run.ts";
import { ensureSkillEvolutionLayout } from "../../src/extensions/skill-evolution/layout.ts";
import {
  AUTHORING_GUIDE_SKILLS,
  proposalSystemPrompt,
} from "../../src/extensions/skill-evolution/prompts.ts";
import {
  buildProposalTools,
  type ProposalCapture,
  ProposalRunError,
  proposalTmpDir,
  REMOTE_BRANCH_PATTERN,
  runProposalAgent,
} from "../../src/extensions/skill-evolution/propose.ts";
import {
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
} from "../../src/extensions/skill-evolution/store.ts";
import { runGit } from "../../src/git/git.ts";
import { listRemoteBranchTips } from "../../src/git/remote.ts";
import { builtinSkillsDir } from "../../src/util/builtin-skills.ts";
import { fileExists } from "../../src/util/markdown-store.ts";
import { commitFile, fakeLogger, makeTempDir, setupRemotePair } from "../git/helpers.ts";
import { proposalFixture } from "./helpers.ts";

const log = fakeLogger();

/** Invoke a tool's execute handler and unwrap its text content (the pi agent-loop shape). */
const invoke = async (tool: ToolDefinition, params: unknown): Promise<string> => {
  const result = await tool.execute(
    "test",
    params as never,
    undefined,
    undefined,
    undefined as never,
  );
  const text = result.content.find(
    (block): block is { type: "text"; text: string } => block.type === "text",
  );

  return text?.text ?? "";
};

const toolNamed = (tools: ToolDefinition[], name: string): ToolDefinition => {
  const tool = tools.find((candidate) => candidate.name === name);

  if (tool == null) throw new Error(`tool ${name} not in surface`);

  return tool;
};

const emptyCapture = (): ProposalCapture => ({ proposals: [] });

describe("buildProposalTools — surface and refusal matrix", () => {
  let base: string;
  let tmpDir: string;
  let capture: ProposalCapture;
  let remoteNames: () => Promise<Set<string>>;

  beforeEach(async () => {
    base = await makeTempDir();
    tmpDir = join(base, "tmp", "skill-evolution");
    await mkdir(tmpDir, { recursive: true });
    await writeFile(join(tmpDir, "notes.txt"), "inside\n", "utf8");
    capture = emptyCapture();
    remoteNames = vi.fn(async () => new Set<string>());
  });

  afterEach(async () => {
    await rm(base, { recursive: true, force: true });
  });

  const tools = () =>
    buildProposalTools({
      workspaceRoot: join(base, "workspace"),
      tmpDir,
      defaultBranch: "main",
      remoteBranchNames: remoteNames,
      capture,
    });

  it("returns exactly the six scoped tools", () => {
    expect(tools().map((tool) => tool.name)).toEqual([
      "read_file",
      "write_file",
      "delete_path",
      "list_dir",
      "git",
      "report_proposals",
    ]);
  });

  it.each([
    {
      name: "read_file escaping with ..",
      tool: "read_file",
      params: () => ({ path: "../secret.txt" }),
    },
    {
      name: "read_file with an absolute outside path",
      tool: "read_file",
      params: () => ({ path: join(base, "workspace", "skills", "deploy", "SKILL.md") }),
    },
    {
      name: "write_file escaping with ..",
      tool: "write_file",
      params: () => ({ path: "../evil.txt", content: "nope" }),
    },
    {
      name: "write_file with an absolute outside path",
      tool: "write_file",
      params: () => ({ path: join(base, "evil.txt"), content: "nope" }),
    },
    {
      name: "delete_path escaping with ..",
      tool: "delete_path",
      params: () => ({ path: "../secret.txt" }),
    },
    {
      name: "delete_path with an absolute outside path",
      tool: "delete_path",
      params: () => ({ path: join(base, "evil.txt") }),
    },
    { name: "list_dir on the parent", tool: "list_dir", params: () => ({ path: ".." }) },
    {
      name: "list_dir with an absolute outside path",
      tool: "list_dir",
      params: () => ({ path: base }),
    },
  ])("$name is refused with instructive text", async ({ tool, params }) => {
    const text = await invoke(toolNamed(tools(), tool), params());

    expect(text).toContain("Refused");
    expect(text).toContain(tmpDir);
    expect(await fileExists(join(base, "secret.txt"))).toBe(false);
    expect(await fileExists(join(base, "evil.txt"))).toBe(false);
  });

  it("read_file and list_dir accept paths inside the tmp dir", async () => {
    const surface = tools();

    await expect(invoke(toolNamed(surface, "read_file"), { path: "notes.txt" })).resolves.toBe(
      "inside\n",
    );
    await expect(invoke(toolNamed(surface, "list_dir"), { path: "." })).resolves.toBe("notes.txt");
  });

  it("delete_path removes a file or a directory (recursively) and names the deleted path", async () => {
    const surface = tools();
    await mkdir(join(tmpDir, "pkg", "references"), { recursive: true });
    await writeFile(join(tmpDir, "pkg", "references", "api.md"), "detail\n", "utf8");

    await expect(invoke(toolNamed(surface, "delete_path"), { path: "notes.txt" })).resolves.toBe(
      "Deleted notes.txt",
    );
    await expect(invoke(toolNamed(surface, "delete_path"), { path: "pkg" })).resolves.toBe(
      "Deleted pkg",
    );

    await expect(fileExists(join(tmpDir, "notes.txt"))).resolves.toBe(false);
    await expect(fileExists(join(tmpDir, "pkg"))).resolves.toBe(false);
  });

  it("delete_path on the tmp dir itself is refused — it holds every worktree", async () => {
    const text = await invoke(toolNamed(tools(), "delete_path"), { path: tmpDir });

    // Like every other path refusal, the guidance names the allowed root.
    expect(text).toContain("Refused");
    expect(text).toContain(tmpDir);
    await expect(fileExists(join(tmpDir, "notes.txt"))).resolves.toBe(true);
  });

  it("delete_path on a missing path is refused with instructive text, not an error", async () => {
    await expect(invoke(toolNamed(tools(), "delete_path"), { path: "nope" })).resolves.toContain(
      "Refused: `nope` does not exist.",
    );
  });

  it.each([
    { name: "checkout", args: ["checkout", "main"] },
    { name: "reset", args: ["reset", "--hard"] },
    { name: "remote", args: ["remote", "get-url", "origin"] },
    { name: "branch", args: ["branch", "-D", "main"] },
    { name: "fetch", args: ["fetch", "origin"] },
    { name: "a global option (-C surgery)", args: ["-C", "/etc", "status"] },
    { name: "empty args", args: [] },
  ])("git $name is refused", async ({ args }) => {
    const text = await invoke(toolNamed(tools(), "git"), { args });

    expect(text).toContain("Refused");
  });

  it.each([
    { name: "a non-namespaced branch", branch: "feature/add-flag" },
    { name: "an uppercase branch", branch: "skill-evolution/Add-Flag" },
    { name: "an underscore branch", branch: "skill-evolution/add_flag" },
    { name: "a nested path", branch: "skill-evolution/deploy/add-flag" },
    { name: "a bare namespace", branch: "skill-evolution/" },
  ])("worktree add with $name is refused with the naming rule", async ({ branch }) => {
    const text = await invoke(toolNamed(tools(), "git"), {
      args: ["worktree", "add", "-b", branch, join(tmpDir, "wt"), "refs/remotes/origin/main"],
    });

    expect(text).toContain("Refused");
    expect(text).toContain("skill-evolution/");
  });

  it.each([
    {
      name: "no -b at all",
      args: () => ["worktree", "add", join(tmpDir, "wt"), "refs/remotes/origin/main"],
    },
    { name: "-b without a value", args: () => ["worktree", "add", "-b"] },
    {
      name: "a path outside the tmp dir",
      args: () => ["worktree", "add", "-b", "skill-evolution/x", join(base, "elsewhere")],
    },
    {
      name: "an unknown option",
      args: () => ["worktree", "add", "--detach", "-b", "skill-evolution/x", join(tmpDir, "wt")],
    },
    { name: "remove without a path", args: () => ["worktree", "remove"] },
    {
      name: "remove with a path outside the tmp dir",
      args: () => ["worktree", "remove", join(base, "elsewhere")],
    },
    { name: "worktree list", args: () => ["worktree", "list"] },
  ])("worktree with $name is refused", async ({ args }) => {
    const text = await invoke(toolNamed(tools(), "git"), { args: args() });

    expect(text).toContain("Refused");
  });

  it("pushing the default branch is refused even when the name matches the namespace", async () => {
    const surface = buildProposalTools({
      workspaceRoot: join(base, "workspace"),
      tmpDir,
      defaultBranch: "skill-evolution/default",
      remoteBranchNames: remoteNames,
      capture,
    });

    const text = await invoke(toolNamed(surface, "git"), {
      args: ["push", "origin", "skill-evolution/default"],
    });

    expect(text).toContain("Refused");
    expect(text).toContain("default branch");
  });

  it.each([
    { name: "a trailing --force", args: ["push", "origin", "skill-evolution/x", "--force"] },
    { name: "a leading --force", args: ["push", "--force", "origin", "skill-evolution/x"] },
    {
      name: "a --force-with-lease",
      args: ["push", "--force-with-lease", "origin", "skill-evolution/x"],
    },
  ])("push with $name is refused — pushes are flag-less", async ({ args }) => {
    const text = await invoke(toolNamed(tools(), "git"), { args });

    expect(text).toContain("Refused");
  });

  it.each([
    { name: "a non-matching name", args: ["push", "origin", "main"] },
    { name: "a non-origin remote", args: ["push", "upstream", "skill-evolution/x"] },
    { name: "a refspec", args: ["push", "origin", "main:skill-evolution/x"] },
    { name: "bare push", args: ["push"] },
  ])("push of $name is refused", async ({ args }) => {
    const text = await invoke(toolNamed(tools(), "git"), { args });

    expect(text).toContain("Refused");
  });

  it.each([
    { name: "add", args: ["add", "skills/deploy/SKILL.md"] },
    { name: "commit", args: ["commit", "-m", "live-tree edit"] },
  ])(
    "git $name without the path parameter is refused — the live tree is never staged or committed",
    async ({ args }) => {
      const text = await invoke(toolNamed(tools(), "git"), { args });

      expect(text).toContain("Refused");
      expect(text).toContain("path");
    },
  );

  it.each([
    { name: "add", args: ["add", "skills/deploy/SKILL.md"] },
    { name: "commit", args: ["commit", "-m", "live-tree edit"] },
  ])("git $name at a tmp-dir path that is not a worktree is refused", async ({ args }) => {
    const text = await invoke(toolNamed(tools(), "git"), {
      args,
      path: join(tmpDir, "plain"),
    });

    expect(text).toContain("Refused");
    expect(text).toContain("worktree");
  });

  it("a name already on the remote is refused with the collision-suffix guidance", async () => {
    remoteNames = vi.fn(async () => new Set(["skill-evolution/taken"]));

    const text = await invoke(toolNamed(tools(), "git"), {
      args: ["push", "origin", "skill-evolution/taken"],
    });

    expect(text).toContain("Refused");
    expect(text).toContain("already exists");
    expect(text).toContain("-2");
  });

  it("git inspection commands accept any path — the check is scoped to add and commit", async () => {
    const text = await invoke(toolNamed(tools(), "git"), {
      args: ["status"],
      path: join(base, "workspace"),
    });

    // No refusal: `path` is a plain cwd for read-only subcommands (git itself fails here — the
    // directory is not a repository — which is the agent's problem, not a guard's).
    expect(text).not.toContain("Refused");
  });

  it.each([
    { name: "add", args: ["add", "skills/deploy/SKILL.md"] },
    { name: "commit", args: ["commit", "-m", "live-tree edit"] },
  ])("git $name with a path outside the tmp dir is refused", async ({ args }) => {
    const text = await invoke(toolNamed(tools(), "git"), { args, path: join(base, "elsewhere") });

    expect(text).toContain("Refused");
    expect(text).toContain(tmpDir);
  });

  it("report_proposals captures its argument into the closure (last call wins)", async () => {
    const surface = tools();

    await expect(
      invoke(toolNamed(surface, "report_proposals"), {
        proposals: [proposalFixture("skill-evolution/deploy-env-flag")],
      }),
    ).resolves.toContain("Recorded 1");
    expect(capture.proposals).toEqual([proposalFixture("skill-evolution/deploy-env-flag")]);

    await invoke(toolNamed(surface, "report_proposals"), { proposals: [] });
    expect(capture.proposals).toEqual([]);
  });

  it("report_proposals refuses malformed entries and captures nothing", async () => {
    const text = await invoke(toolNamed(tools(), "report_proposals"), {
      proposals: [proposalFixture("feature/not-a-proposal")],
    });

    expect(text).toContain("Refused");
    expect(capture.proposals).toEqual([]);
  });

  it.each(
    (["skill", "pattern", "description", "problem", "rootCause", "evidence"] as const).flatMap(
      (field) => [
        { field, value: "" },
        { field, value: " \t " },
      ],
    ),
  )(
    "report_proposals refuses an empty or whitespace-only $field and captures nothing",
    async ({ field, value }) => {
      const text = await invoke(toolNamed(tools(), "report_proposals"), {
        proposals: [proposalFixture("skill-evolution/deploy-env-flag", { [field]: value })],
      });

      // Whitespace-only is as blank as absent (the tasks `update_goal` trim rule).
      expect(text).toContain("Refused");
      expect(text).toContain(field);
      expect(capture.proposals).toEqual([]);
    },
  );

  it("the report_proposals description names every field and its pattern-page source", () => {
    const description = toolNamed(tools(), "report_proposals").description ?? "";

    for (const field of [
      "branch",
      "skill",
      "pattern",
      "description",
      "problem",
      "rootCause",
      "evidence",
    ]) {
      expect(description).toContain(field);
    }

    expect(description).toContain("pattern page");
  });
});

describe("buildProposalTools — happy paths (real git, bare origin)", () => {
  let base: string;
  let ws: string;
  let tmpDir: string;
  let capture: ProposalCapture;

  beforeEach(async () => {
    base = await makeTempDir();
    const { cloneA } = await setupRemotePair(base);
    ws = cloneA;
    // Production shape: the worktrees live outside the repo, under the OS temp dir.
    tmpDir = proposalTmpDir(ws);
    await mkdir(tmpDir, { recursive: true });
    capture = emptyCapture();
  });

  afterEach(async () => {
    await runGit(ws, ["worktree", "prune"]);
    await rm(tmpDir, { recursive: true, force: true });
    await rm(base, { recursive: true, force: true });
  });

  const tools = () =>
    buildProposalTools({
      workspaceRoot: ws,
      tmpDir,
      defaultBranch: "main",
      remoteBranchNames: vi.fn(async () => new Set<string>()),
      capture,
    });

  const worktree = (slug: string): string => join(tmpDir, slug);

  const cutWorktree = async (slug: string): Promise<string> => {
    const path = worktree(slug);
    const text = await invoke(toolNamed(tools(), "git"), {
      args: ["worktree", "add", "-b", `skill-evolution/${slug}`, path, "refs/remotes/origin/main"],
    });

    expect(text).toMatch(/^exit 0/);
    return path;
  };

  it("worktree add cuts the branch and checks out a worktree under the tmp dir", async () => {
    await cutWorktree("deploy-env-flag");

    expect(
      await runGit(ws, ["branch", "--list", "skill-evolution/*", "--format=%(refname:short)"]),
    ).toBe("skill-evolution/deploy-env-flag");

    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["status"], path: worktree("deploy-env-flag") }),
    ).resolves.toMatch(/^exit 0/);
  });

  it.each(["status", "diff", "log", "show"] as const)(
    "git %s runs at the workspace root",
    async (subcommand) => {
      await expect(invoke(toolNamed(tools(), "git"), { args: [subcommand] })).resolves.toMatch(
        /^exit 0/,
      );
    },
  );

  it("write_file + add + commit target a worktree via the path parameter", async () => {
    const path = await cutWorktree("deploy-env-flag");

    await expect(
      invoke(toolNamed(tools(), "write_file"), {
        path: join(path, "skills", "deploy", "SKILL.md"),
        content: "# Deploy\n\nAdd the --env flag.\n",
      }),
    ).resolves.toContain("Wrote");

    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["add", "skills/deploy/SKILL.md"], path }),
    ).resolves.toMatch(/^exit 0/);

    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["commit", "-m", "deploy: add --env flag"], path }),
    ).resolves.toMatch(/^exit 0/);

    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["log", "-1", "--format=%s"], path }),
    ).resolves.toContain("deploy: add --env flag");
  });

  it("add and commit accept a path inside the worktree, not only its root", async () => {
    const path = await cutWorktree("deploy-env-flag");

    await expect(
      invoke(toolNamed(tools(), "write_file"), {
        path: join(path, "skills", "deploy", "SKILL.md"),
        content: "# Deploy\n\nAdd the --env flag.\n",
      }),
    ).resolves.toContain("Wrote");

    await expect(
      invoke(toolNamed(tools(), "git"), {
        args: ["add", "SKILL.md"],
        path: join(path, "skills", "deploy"),
      }),
    ).resolves.toMatch(/^exit 0/);

    await expect(
      invoke(toolNamed(tools(), "git"), {
        args: ["commit", "-m", "deploy: add --env flag"],
        path: join(path, "skills", "deploy"),
      }),
    ).resolves.toMatch(/^exit 0/);
  });

  it("a relative path runs the mutating pair where the guard bound it — never against the workspace", async () => {
    const path = await cutWorktree("deploy-env-flag");

    await expect(
      invoke(toolNamed(tools(), "write_file"), {
        path: join(path, "skills", "deploy", "SKILL.md"),
        content: "# Deploy\n\nRelative edit.\n",
      }),
    ).resolves.toContain("Wrote");

    // `path` resolves against the tmp dir for add/commit (the validated base) — the same
    // relative string must never execute against a same-named workspace directory.
    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["add", "-A"], path: "deploy-env-flag" }),
    ).resolves.toMatch(/^exit 0/);
    await expect(
      invoke(toolNamed(tools(), "git"), {
        args: ["commit", "-m", "relative-path edit"],
        path: "deploy-env-flag",
      }),
    ).resolves.toMatch(/^exit 0/);

    // The commit landed on the proposal branch, not on the live tree.
    await expect(
      runGit(ws, ["log", "-1", "--format=%s", "skill-evolution/deploy-env-flag"]),
    ).resolves.toContain("relative-path edit");
    await expect(runGit(ws, ["log", "-1", "--format=%s"])).resolves.not.toContain(
      "relative-path edit",
    );
    expect(await runGit(ws, ["status", "--porcelain"])).toBe("");
  });

  it("a planted .git marker under the tmp dir cannot redirect add into the live tree", async () => {
    // The attack: write_file happily creates tmpDir/skills/.git/HEAD, making tmpDir/skills
    // look like a worktree to the guard; a relative path "skills" then executes against the
    // workspace's same-named directory if validation and execution ever use different bases.
    await expect(
      invoke(toolNamed(tools(), "write_file"), {
        path: join(tmpDir, "skills", ".git", "HEAD"),
        content: "ref: refs/heads/main\n",
      }),
    ).resolves.toContain("Wrote");
    await mkdir(join(ws, "skills"), { recursive: true });
    await writeFile(join(ws, "skills", "SKILL.md"), "live-tree content\n", "utf8");

    // Not refused (the marker satisfies the worktree check) — but it runs at the validated
    // tmp-dir binding, where the fake repository makes git fail harmlessly.
    const text = await invoke(toolNamed(tools(), "git"), { args: ["add", "-A"], path: "skills" });

    expect(text).not.toContain("Refused");
    // The live tree staged nothing: the workspace file is still untracked, the index empty.
    expect(await runGit(ws, ["diff", "--cached", "--name-only"])).toBe("");
    expect(await runGit(ws, ["status", "--porcelain"])).toBe("?? skills/");
  });

  it("worktree add with a relative positional is refused — it would land inside the live tree", async () => {
    const text = await invoke(toolNamed(tools(), "git"), {
      args: ["worktree", "add", "-b", "skill-evolution/in-live", "in-live"],
    });

    expect(text).toContain("Refused");
    expect(text).toContain(tmpDir);
    await expect(fileExists(join(ws, "in-live"))).resolves.toBe(false);
  });

  it("push origin <name> creates the branch on the remote", async () => {
    const path = await cutWorktree("deploy-env-flag");
    await mkdir(join(path, "skills", "deploy"), { recursive: true });
    await writeFile(join(path, "skills", "deploy", "SKILL.md"), "# Deploy\n", "utf8");
    await runGit(path, ["add", "-A"]);
    await runGit(path, ["-c", "core.editor=true", "commit", "-m", "deploy: add --env flag"]);

    await expect(
      invoke(toolNamed(tools(), "git"), {
        args: ["push", "origin", "skill-evolution/deploy-env-flag"],
        path,
      }),
    ).resolves.toMatch(/^exit 0/);

    const tips = await listRemoteBranchTips(ws, REMOTE_BRANCH_PATTERN);

    expect(tips.size).toBe(1);
    expect(tips.get("skill-evolution/deploy-env-flag")).toMatch(/^[0-9a-f]{40}$/);
  });

  it("delete_path + add + commit + push author a whole-skill removal on the branch", async () => {
    // The base branch must carry the skill the removal retires — the worktree is cut from it.
    await mkdir(join(ws, "skills", "deploy"), { recursive: true });
    await commitFile(ws, "skills/deploy/SKILL.md", "# Deploy\n\nGuidance.\n", "seed deploy skill");
    await runGit(ws, ["push", "origin", "main"]);

    const path = await cutWorktree("deploy-retire-skill");

    await expect(
      invoke(toolNamed(tools(), "delete_path"), { path: join(path, "skills", "deploy") }),
    ).resolves.toContain("Deleted");

    // Deletions stage with the same add — `git add` records removals, not just modifications.
    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["add", "skills/deploy"], path }),
    ).resolves.toMatch(/^exit 0/);
    await expect(
      invoke(toolNamed(tools(), "git"), {
        args: ["commit", "-m", "deploy: remove the retired skill"],
        path,
      }),
    ).resolves.toMatch(/^exit 0/);
    await expect(
      invoke(toolNamed(tools(), "git"), {
        args: ["push", "origin", "skill-evolution/deploy-retire-skill"],
        path,
      }),
    ).resolves.toMatch(/^exit 0/);

    // The pushed diff is the deletion itself, confined to skills/.
    const tip = (await listRemoteBranchTips(ws, REMOTE_BRANCH_PATTERN)).get(
      "skill-evolution/deploy-retire-skill",
    );

    expect(tip).toMatch(/^[0-9a-f]{40}$/);
    expect(await runGit(ws, ["diff", "--name-only", `refs/remotes/origin/main...${tip}`])).toBe(
      "skills/deploy/SKILL.md",
    );
  });

  it("deleting a worktree's .git marker self-limits — add is then refused", async () => {
    const path = await cutWorktree("deploy-env-flag");

    await expect(
      invoke(toolNamed(tools(), "delete_path"), { path: join(path, ".git") }),
    ).resolves.toContain("Deleted");

    // The directory no longer reads as a worktree, so the mutating pair refuses instead of
    // staging anywhere else; the host sweep (prune + tmp-dir removal) cleans up afterwards.
    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["add", "-A"], path }),
    ).resolves.toContain("Refused");
  });

  it("worktree remove removes the worktree", async () => {
    const path = await cutWorktree("deploy-env-flag");

    await expect(
      invoke(toolNamed(tools(), "git"), { args: ["worktree", "remove", path] }),
    ).resolves.toMatch(/^exit 0/);

    const worktrees = (await runGit(ws, ["worktree", "list", "--porcelain"])).split("\n");
    expect(worktrees.some((line) => line === `worktree ${path}`)).toBe(false);
  });
});

describe("runProposalAgent (faked SideRunner)", () => {
  let base: string;

  beforeEach(async () => {
    base = await makeTempDir();
  });

  afterEach(async () => {
    await rm(base, { recursive: true, force: true });
  });

  /** A workspace-shaped fixture: one pattern page, one skill, an open ledger row. */
  const agentWorkspace = async (): Promise<{ workspaceRoot: string; tmpDir: string }> => {
    const workspaceRoot = join(base, "workspace");
    const tmpDir = proposalTmpDir(workspaceRoot);
    await ensureSkillEvolutionLayout(workspaceRoot, log);
    await writeFile(
      join(workspaceRoot, "memories", "skill-evolution", "deploy-env-flag.md"),
      "# deploy-env-flag\n\n## Problem\n--env flag missing.\n",
      "utf8",
    );
    await mkdir(join(workspaceRoot, "skills", "deploy"), { recursive: true });
    await writeFile(
      join(workspaceRoot, "skills", "deploy", "SKILL.md"),
      "# Deploy\n\nGuidance without the flag.\n",
      "utf8",
    );

    return { workspaceRoot, tmpDir };
  };

  const impactLog: ImpactLogEntry[] = [];

  it("passes the isolated six-tool surface with the proposal system prompt", async () => {
    const { workspaceRoot, tmpDir } = await agentWorkspace();
    const run = vi.fn(
      async (_options: HeadlessRunOptions): Promise<{ text: string }> => ({
        text: "",
      }),
    );

    await runProposalAgent({
      side: { run },
      workspaceRoot,
      tmpDir,
      defaultBranch: "main",
      eligible: ["deploy-env-flag.md"],
      impactLog: [
        {
          date: "2026-08-29",
          skill: "commit",
          pattern: "commit-msg.md",
          branch: "skill-evolution/commit-msg",
          tip: "abc123",
          description: "Tighten the message guidance",
          status: IMPACT_LOG_STATUSES.proposed,
        },
      ],
      log,
    });

    expect(run).toHaveBeenCalledTimes(1);

    const [options] = run.mock.calls[0] ?? [];

    // isolatePrompt over the default empty built-in allowlist: the six custom tools are the
    // entire surface.
    expect(options?.isolatePrompt).toBe(true);
    expect(options?.tier).toBe("processor");
    expect(options?.tools).toBeUndefined();
    expect(options?.customTools?.map((tool) => tool.name)).toEqual([
      "read_file",
      "write_file",
      "delete_path",
      "list_dir",
      "git",
      "report_proposals",
    ]);
    expect(options?.system).toBe(proposalSystemPrompt(workspaceRoot, tmpDir, "main"));

    // The authoring guides ride the run as actual skills: skillPaths points at the real bundled
    // builtin-skills dir (pins the neutral-module constant from src/util/), and both guide names
    // are force-loaded from pi's catalog — no guide content is assembled here.
    expect(options?.skillPaths).toEqual([builtinSkillsDir]);
    expect(options?.forceLoadSkills).toEqual([...AUTHORING_GUIDE_SKILLS]);

    // The prompt carries the three inputs: pattern pages, the ledger, the skills inventory.
    expect(options?.prompt).toContain("### deploy-env-flag.md");
    expect(options?.prompt).toContain("--env flag missing");
    expect(options?.prompt).toContain("skill-evolution/commit-msg");
    expect(options?.prompt).toContain("### skills/deploy");
    expect(options?.prompt).toContain("Guidance without the flag.");

    // The task line names the full report payload, reasoning included.
    expect(options?.prompt).toContain(
      "(branch, skill, pattern, description, problem, rootCause, evidence)",
    );
    expect(options?.prompt).toContain("problem`/`rootCause`/`evidence`");
  });

  it("returns the payload of a run whose fake invokes the captured report_proposals tool", async () => {
    const { workspaceRoot, tmpDir } = await agentWorkspace();
    const run = vi.fn(async (options: HeadlessRunOptions): Promise<{ text: string }> => {
      const report = options.customTools?.find((tool) => tool.name === "report_proposals");

      if (report != null) {
        await report.execute(
          "test",
          { proposals: [proposalFixture("skill-evolution/deploy-env-flag")] } as never,
          undefined,
          undefined,
          undefined as never,
        );
      }

      return { text: "" };
    });

    const reported = await runProposalAgent({
      side: { run },
      workspaceRoot,
      tmpDir,
      defaultBranch: "main",
      eligible: ["deploy-env-flag.md"],
      impactLog,
      log,
    });

    expect(reported).toEqual([proposalFixture("skill-evolution/deploy-env-flag")]);
  });

  it("returns an empty list when the run never reports (a clean no-proposal outcome)", async () => {
    const { workspaceRoot, tmpDir } = await agentWorkspace();

    await expect(
      runProposalAgent({
        side: {
          run: vi.fn(async (): Promise<{ text: string }> => ({ text: "nothing worth proposing" })),
        },
        workspaceRoot,
        tmpDir,
        defaultBranch: "main",
        eligible: ["deploy-env-flag.md"],
        impactLog,
        log,
      }),
    ).resolves.toEqual([]);
  });

  it("a run whose eligible page vanished mid-run still assembles a prompt", async () => {
    const { workspaceRoot, tmpDir } = await agentWorkspace();
    const run = vi.fn(
      async (_options: HeadlessRunOptions): Promise<{ text: string }> => ({
        text: "",
      }),
    );

    await runProposalAgent({
      side: { run },
      workspaceRoot,
      tmpDir,
      defaultBranch: "main",
      eligible: ["gone.md"],
      impactLog,
      log,
    });

    const [options] = run.mock.calls[0] ?? [];

    expect(log.warn).toHaveBeenCalled();
    expect(options?.prompt).toContain("(none — report an empty proposal list and stop)");
  });

  it("a dying run throws ProposalRunError carrying whatever was reported before the death", async () => {
    const { workspaceRoot, tmpDir } = await agentWorkspace();
    const run = vi.fn(async (options: HeadlessRunOptions): Promise<{ text: string }> => {
      const report = options.customTools?.find((tool) => tool.name === "report_proposals");

      if (report != null) {
        await report.execute(
          "test",
          { proposals: [proposalFixture("skill-evolution/deploy-env-flag")] } as never,
          undefined,
          undefined,
          undefined as never,
        );
      }

      // The run dies AFTER reporting — e.g. the next proposal's model call failed.
      throw new Error("model call failed mid-run");
    });

    let thrown: unknown;
    try {
      await runProposalAgent({
        side: { run },
        workspaceRoot,
        tmpDir,
        defaultBranch: "main",
        eligible: ["deploy-env-flag.md"],
        impactLog,
        log,
      });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(ProposalRunError);
    expect((thrown as ProposalRunError).proposals).toEqual([
      proposalFixture("skill-evolution/deploy-env-flag"),
    ]);
    expect((thrown as ProposalRunError).cause).toBeInstanceOf(Error);
    expect((thrown as ProposalRunError).message).toContain("model call failed mid-run");
  });
});

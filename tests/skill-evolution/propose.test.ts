import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HeadlessRunOptions } from "../../src/agent/side-run.ts";
import { ensureSkillEvolutionLayout } from "../../src/extensions/skill-evolution/layout.ts";
import { proposalSystemPrompt } from "../../src/extensions/skill-evolution/prompts.ts";
import {
  buildProposalTools,
  type ProposalCapture,
  ProposalRunError,
  REMOTE_BRANCH_PATTERN,
  type ReportedProposal,
  runProposalAgent,
} from "../../src/extensions/skill-evolution/propose.ts";
import {
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
} from "../../src/extensions/skill-evolution/store.ts";
import { runGit } from "../../src/git/git.ts";
import { listRemoteBranchTips } from "../../src/git/remote.ts";
import { fileExists } from "../../src/util/markdown-store.ts";
import { fakeLogger, makeTempDir, setupRemotePair } from "../git/helpers.ts";

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

const proposal = (branch: string): ReportedProposal => ({
  branch,
  skill: "deploy",
  pattern: "deploy-env-flag.md",
  description: "Add the --env flag to the deploy guidance",
});

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

  it("returns exactly the five scoped tools", () => {
    expect(tools().map((tool) => tool.name)).toEqual([
      "read_file",
      "write_file",
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
    const text = await invoke(toolNamed(tools(), "git"), { args, path: "." });

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

  it("git with a path parameter outside the tmp dir is refused", async () => {
    const text = await invoke(toolNamed(tools(), "git"), {
      args: ["status"],
      path: join(base, "workspace"),
    });

    expect(text).toContain("Refused");
    expect(text).toContain(tmpDir);
  });

  it("report_proposals captures its argument into the closure (last call wins)", async () => {
    const surface = tools();

    await expect(
      invoke(toolNamed(surface, "report_proposals"), {
        proposals: [proposal("skill-evolution/deploy-env-flag")],
      }),
    ).resolves.toContain("Recorded 1");
    expect(capture.proposals).toEqual([proposal("skill-evolution/deploy-env-flag")]);

    await invoke(toolNamed(surface, "report_proposals"), { proposals: [] });
    expect(capture.proposals).toEqual([]);
  });

  it("report_proposals refuses malformed entries and captures nothing", async () => {
    const text = await invoke(toolNamed(tools(), "report_proposals"), {
      proposals: [proposal("feature/not-a-proposal")],
    });

    expect(text).toContain("Refused");
    expect(capture.proposals).toEqual([]);
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
    tmpDir = join(ws, ".tachikoma", "tmp", "skill-evolution");
    await mkdir(tmpDir, { recursive: true });
    capture = emptyCapture();
  });

  afterEach(async () => {
    await runGit(ws, ["worktree", "prune"]);
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
    const tmpDir = join(workspaceRoot, ".tachikoma", "tmp", "skill-evolution");
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

  it("passes the isolated five-tool surface with the proposal system prompt", async () => {
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

    // isolatePrompt over the default empty built-in allowlist: the five custom tools are the
    // entire surface.
    expect(options?.isolatePrompt).toBe(true);
    expect(options?.tier).toBe("processor");
    expect(options?.tools).toBeUndefined();
    expect(options?.customTools?.map((tool) => tool.name)).toEqual([
      "read_file",
      "write_file",
      "list_dir",
      "git",
      "report_proposals",
    ]);
    expect(options?.system).toBe(proposalSystemPrompt(workspaceRoot, tmpDir, "main"));

    // The prompt carries the three inputs: pattern pages, the ledger, the skills inventory.
    expect(options?.prompt).toContain("### deploy-env-flag.md");
    expect(options?.prompt).toContain("--env flag missing");
    expect(options?.prompt).toContain("skill-evolution/commit-msg");
    expect(options?.prompt).toContain("### skills/deploy");
    expect(options?.prompt).toContain("Guidance without the flag.");
  });

  it("returns the payload of a run whose fake invokes the captured report_proposals tool", async () => {
    const { workspaceRoot, tmpDir } = await agentWorkspace();
    const run = vi.fn(async (options: HeadlessRunOptions): Promise<{ text: string }> => {
      const report = options.customTools?.find((tool) => tool.name === "report_proposals");

      if (report != null) {
        await report.execute(
          "test",
          { proposals: [proposal("skill-evolution/deploy-env-flag")] } as never,
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

    expect(reported).toEqual([proposal("skill-evolution/deploy-env-flag")]);
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
          { proposals: [proposal("skill-evolution/deploy-env-flag")] } as never,
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
      proposal("skill-evolution/deploy-env-flag"),
    ]);
    expect((thrown as ProposalRunError).cause).toBeInstanceOf(Error);
    expect((thrown as ProposalRunError).message).toContain("model call failed mid-run");
  });
});

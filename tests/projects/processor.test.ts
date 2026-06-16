import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GitApi, PostProcessorContext } from "../../src/extensions/api.ts";
import { createProjectsProcessor } from "../../src/extensions/projects/processor.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { commitAll } from "../../src/git/commit.ts";
import type { CommitAgent } from "../../src/git/commit-agent.ts";
import { runGit } from "../../src/git/git.ts";
import { smartPull, smartPush } from "../../src/git/sync.ts";
import {
  agentCommittingAs,
  agentThatThrows,
  configureIdentity,
  createProjectOrigin,
  createWorkspace,
  fakeLogger,
  headOf,
  lastSubject,
  makeTempDir,
  resolvingResolver,
} from "./helpers.ts";

const context = (): PostProcessorContext => ({
  trunk: null,
  transcriptPath: null,
  log: fakeLogger(),
});

const git: GitApi = {
  commitAll: (options) => commitAll({ ...options, log: options.log ?? fakeLogger() }),
  createCommitAgent: () => (async () => {}) as CommitAgent,
  smartPush: (cwd, remote, branch, options) =>
    smartPush(cwd, remote, branch, options?.log ?? fakeLogger(), options?.resolver),
  smartPull: (cwd, remote, branch, options) =>
    smartPull(cwd, remote, branch, options?.log ?? fakeLogger(), options?.resolver),
};

let base: string;
let workspace: string;
let origin: string;
let projectPath: string;

beforeEach(async () => {
  base = await makeTempDir();
  origin = await createProjectOrigin(base, "app");
  workspace = await createWorkspace(base);

  await handleRegisterProject(
    { workspaceRoot: workspace, log: fakeLogger() },
    {
      name: "app",
      url: origin,
    },
  );

  projectPath = join(workspace, "projects", "app");
  await configureIdentity(projectPath);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("projects processor", () => {
  it("commits and pushes each dirty project via the agent", async () => {
    await writeFile(join(projectPath, "feature.ts"), "export const x = 1;\n", "utf8");

    await createProjectsProcessor({
      workspaceRoot: workspace,
      agent: agentCommittingAs("Add feature module"),
      git,
    }).process(context());

    expect(await lastSubject(projectPath)).toBe("Add feature module");
    expect(await runGit(projectPath, ["status", "--porcelain"])).toBe("");
    expect(await headOf(origin)).toBe(await headOf(projectPath));
  });

  it("leaves clean projects untouched and does not invoke the agent", async () => {
    const recorder = { calls: 0 };
    const agent: CommitAgent = async () => {
      recorder.calls += 1;
    };
    const head = await headOf(projectPath);

    await createProjectsProcessor({ workspaceRoot: workspace, agent, git }).process(context());

    expect(recorder.calls).toBe(0);
    expect(await headOf(projectPath)).toBe(head);
  });

  it("pushes a clean project that is ahead of its remote without committing", async () => {
    // Commit a change in the project so it sits ahead of origin with a clean tree.
    await writeFile(join(projectPath, "note.md"), "ahead\n", "utf8");
    await runGit(projectPath, ["add", "-A"]);
    await runGit(projectPath, ["commit", "-m", "local work"]);
    const projectHead = await headOf(projectPath);
    expect(await headOf(origin)).not.toBe(projectHead);

    // A clean tree is never committed, so the agent is never invoked.
    const recorder = { calls: 0 };
    const agent: CommitAgent = async () => {
      recorder.calls += 1;
    };

    await createProjectsProcessor({ workspaceRoot: workspace, agent, git }).process(context());

    expect(recorder.calls).toBe(0);
    expect(await lastSubject(projectPath)).toBe("local work");
    expect(await headOf(projectPath)).toBe(projectHead);
    expect(await headOf(origin)).toBe(projectHead);
  });

  it("falls back to a deterministic message when the agent fails", async () => {
    await writeFile(join(projectPath, "feature.ts"), "export const x = 1;\n", "utf8");

    await createProjectsProcessor({
      workspaceRoot: workspace,
      agent: agentThatThrows(new Error("model down")),
      git,
    }).process(context());

    expect(await lastSubject(projectPath)).toMatch(/^Update app files \(\d{4}-\d{2}-\d{2}\)$/);
    expect(await headOf(origin)).toBe(await headOf(projectPath));
  });

  it("invokes the resolver and pushes when a project push hits a rebase conflict", async () => {
    // The remote advances README.md on the same region the local change touches.
    const seeder = join(base, "seeder");
    await runGit(base, ["clone", origin, seeder]);
    await configureIdentity(seeder);
    await writeFile(join(seeder, "README.md"), "remote edit\n", "utf8");
    await runGit(seeder, ["add", "README.md"]);
    await runGit(seeder, ["commit", "-m", "Remote edit"]);
    await runGit(seeder, ["push", "origin", "main"]);

    // A dirty local change to the same region; the processor commits then pushes it.
    await writeFile(join(projectPath, "README.md"), "local edit\n", "utf8");

    const agent = agentCommittingAs("Local edit");
    const resolver = vi.fn(resolvingResolver);

    await createProjectsProcessor({ workspaceRoot: workspace, agent, git, resolver }).process(
      context(),
    );

    expect(resolver).toHaveBeenCalledWith(projectPath, "origin/main", expect.anything());
    // The rebased-and-resolved commit landed on the remote.
    expect(await headOf(origin)).toBe(await headOf(projectPath));
  });
});

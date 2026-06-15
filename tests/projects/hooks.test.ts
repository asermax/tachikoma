import { access, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GitApi } from "../../src/extensions/api.ts";
import { currentBranch } from "../../src/extensions/projects/git.ts";
import { syncProjects } from "../../src/extensions/projects/hooks.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { commitAll } from "../../src/git/commit.ts";
import { runGit } from "../../src/git/git.ts";
import { smartPull, smartPush } from "../../src/git/sync.ts";
import {
  commitFile,
  configureIdentity,
  createProjectOrigin,
  createWorkspace,
  fakeLogger,
  headOf,
  makeTempDir,
  resolvingResolver,
} from "./helpers.ts";

const log = fakeLogger();

const git: GitApi = {
  commitAll: (options) => commitAll({ ...options, log: options.log ?? log }),
  smartPush: (cwd, remote, branch, options) =>
    smartPush(cwd, remote, branch, options?.log ?? log, options?.resolver),
  smartPull: (cwd, remote, branch, options) =>
    smartPull(cwd, remote, branch, options?.log ?? log, options?.resolver),
};

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = await createWorkspace(base);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("syncProjects", () => {
  it("creates the projects directory and returns quietly with no submodules", async () => {
    await syncProjects(workspace, git, log);

    await expect(access(join(workspace, "projects"))).resolves.toBeUndefined();
  });

  it("initializes and checks out submodules in a fresh workspace clone", async () => {
    const origin = await createProjectOrigin(base, "app");
    await handleRegisterProject({ workspaceRoot: workspace, log }, { name: "app", url: origin });
    await runGit(workspace, ["commit", "-m", "Register app project"]);

    const clone = join(base, "workspace-clone");
    await runGit(base, ["clone", workspace, clone]);
    await configureIdentity(clone);

    await syncProjects(clone, git, log);

    const clonedProject = join(clone, "projects", "app");
    await expect(access(join(clonedProject, "README.md"))).resolves.toBeUndefined();
    expect(await currentBranch(clonedProject)).toBe("main");
  });

  it("fast-forwards an initialized submodule that is behind its remote", async () => {
    const origin = await createProjectOrigin(base, "app");
    await handleRegisterProject({ workspaceRoot: workspace, log }, { name: "app", url: origin });

    const seeder = join(base, "seeder");
    await runGit(base, ["clone", origin, seeder]);
    await configureIdentity(seeder);
    await commitFile(seeder, "update.txt", "fresh\n", "Remote update");
    await runGit(seeder, ["push", "origin", "main"]);

    await syncProjects(workspace, git, log);

    const projectPath = join(workspace, "projects", "app");
    await expect(access(join(projectPath, "update.txt"))).resolves.toBeUndefined();
    expect(await headOf(projectPath)).toBe(await headOf(seeder));
  });

  it("invokes the resolver and advances when a submodule pull hits a rebase conflict", async () => {
    const origin = await createProjectOrigin(base, "app");
    await handleRegisterProject({ workspaceRoot: workspace, log }, { name: "app", url: origin });

    const projectPath = join(workspace, "projects", "app");
    await configureIdentity(projectPath);

    // A local commit that diverges from the origin on the same file/region.
    await writeFile(join(projectPath, "README.md"), "local edit\n", "utf8");
    await runGit(projectPath, ["add", "README.md"]);
    await runGit(projectPath, ["commit", "-m", "Local edit"]);

    // The remote advances the same region, so pulling produces a conflict.
    const seeder = join(base, "seeder");
    await runGit(base, ["clone", origin, seeder]);
    await configureIdentity(seeder);
    await writeFile(join(seeder, "README.md"), "remote edit\n", "utf8");
    await runGit(seeder, ["add", "README.md"]);
    await runGit(seeder, ["commit", "-m", "Remote edit"]);
    await runGit(seeder, ["push", "origin", "main"]);

    const resolver = vi.fn(resolvingResolver);
    const before = await headOf(projectPath);

    await syncProjects(workspace, git, log, resolver);

    expect(resolver).toHaveBeenCalledWith(projectPath, "origin/main", expect.anything());
    expect(await headOf(projectPath)).not.toBe(before);
    expect(await readFile(join(projectPath, "README.md"), "utf8")).toBe("merged by agent\n");
  });
});

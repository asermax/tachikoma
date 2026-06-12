import { access, rm } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runGit } from "../../src/extensions/git/git.ts";
import { currentBranch } from "../../src/extensions/projects/git.ts";
import { syncProjects } from "../../src/extensions/projects/hooks.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import {
  commitFile,
  configureIdentity,
  createProjectOrigin,
  createWorkspace,
  fakeLogger,
  headOf,
  makeTempDir,
} from "./helpers.ts";

const log = fakeLogger();

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
    await syncProjects(workspace, log);

    await expect(access(join(workspace, "projects"))).resolves.toBeUndefined();
  });

  it("initializes and checks out submodules in a fresh workspace clone", async () => {
    const origin = await createProjectOrigin(base, "app");
    await handleRegisterProject({ workspaceRoot: workspace, log }, { name: "app", url: origin });
    await runGit(workspace, ["commit", "-m", "Register app project"]);

    const clone = join(base, "workspace-clone");
    await runGit(base, ["clone", workspace, clone]);
    await configureIdentity(clone);

    await syncProjects(clone, log);

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

    await syncProjects(workspace, log);

    const projectPath = join(workspace, "projects", "app");
    await expect(access(join(projectPath, "update.txt"))).resolves.toBeUndefined();
    expect(await headOf(projectPath)).toBe(await headOf(seeder));
  });
});

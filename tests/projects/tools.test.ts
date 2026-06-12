import { access, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runGit } from "../../src/extensions/git/git.ts";
import { currentBranch, listSubmodules } from "../../src/extensions/projects/git.ts";
import {
  handleDeregisterProject,
  handleListProjects,
  handleRegisterProject,
  type ProjectToolDeps,
} from "../../src/extensions/projects/tools.ts";
import { createProjectOrigin, createWorkspace, fakeLogger, makeTempDir } from "./helpers.ts";

let base: string;
let workspace: string;
let origin: string;
let deps: ProjectToolDeps;

beforeEach(async () => {
  base = await makeTempDir();
  origin = await createProjectOrigin(base, "app");
  workspace = await createWorkspace(base);
  deps = { workspaceRoot: workspace, log: fakeLogger() };
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("handleRegisterProject", () => {
  it("adds the submodule and checks out its default branch", async () => {
    const output = await handleRegisterProject(deps, { name: "app", url: origin });

    expect(output).toContain("Registered project 'app' (branch: main)");
    await expect(access(join(workspace, "projects", "app", "README.md"))).resolves.toBeUndefined();
    expect(await currentBranch(join(workspace, "projects", "app"))).toBe("main");
    expect(await listSubmodules(workspace)).toEqual(["projects/app"]);
    expect(
      await runGit(workspace, ["config", "-f", ".gitmodules", "submodule.projects/app.url"]),
    ).toBe(origin);
  });

  it("rejects a duplicate registration", async () => {
    await handleRegisterProject(deps, { name: "app", url: origin });

    await expect(handleRegisterProject(deps, { name: "app", url: origin })).rejects.toThrow(
      /already exists/,
    );
  });

  it("rejects empty arguments", async () => {
    await expect(handleRegisterProject(deps, { name: "", url: origin })).rejects.toThrow(
      /'name' is required/,
    );
    await expect(handleRegisterProject(deps, { name: "app", url: "" })).rejects.toThrow(
      /'url' is required/,
    );
  });
});

describe("handleDeregisterProject", () => {
  beforeEach(async () => {
    await handleRegisterProject(deps, { name: "app", url: origin });
  });

  it("removes a clean project", async () => {
    const output = await handleDeregisterProject(deps, { name: "app" });

    expect(output).toContain("Deregistered project 'app'");
    expect(await listSubmodules(workspace)).toEqual([]);
    await expect(access(join(workspace, "projects", "app"))).rejects.toThrow();
  });

  it("refuses to remove a project with uncommitted changes", async () => {
    await writeFile(join(workspace, "projects", "app", "wip.txt"), "wip\n", "utf8");

    await expect(handleDeregisterProject(deps, { name: "app" })).rejects.toThrow(
      /uncommitted changes/,
    );
    expect(await listSubmodules(workspace)).toEqual(["projects/app"]);
  });

  it("removes a dirty project when forced", async () => {
    await writeFile(join(workspace, "projects", "app", "wip.txt"), "wip\n", "utf8");

    await handleDeregisterProject(deps, { name: "app", force: true });

    expect(await listSubmodules(workspace)).toEqual([]);
  });

  it("rejects an unknown project", async () => {
    await expect(handleDeregisterProject(deps, { name: "ghost" })).rejects.toThrow(/not found/);
  });
});

describe("handleListProjects", () => {
  it("reports when no projects are registered", async () => {
    expect(await handleListProjects(deps)).toContain("No projects registered");
  });

  it("lists projects with branch and dirty state", async () => {
    await handleRegisterProject(deps, { name: "app", url: origin });
    await writeFile(join(workspace, "projects", "app", "wip.txt"), "wip\n", "utf8");

    const output = await handleListProjects(deps);

    expect(output).toContain("- app: main — 1 uncommitted change");
  });
});

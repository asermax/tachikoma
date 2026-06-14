import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runGit } from "../../src/extensions/git/git.ts";
import { buildProjectsContext } from "../../src/extensions/projects/context-provider.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { createProjectOrigin, createWorkspace, fakeLogger, makeTempDir } from "./helpers.ts";

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = await createWorkspace(base);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("buildProjectsContext", () => {
  it("includes usage guidance and notes nothing registered", async () => {
    const content = await buildProjectsContext(workspace, fakeLogger());

    expect(content).toContain("## Projects");
    expect(content).toContain("list_projects");
    expect(content).toContain("No projects are currently registered.");
  });

  it("lists each project with its branch", async () => {
    const origin = await createProjectOrigin(base, "app");
    await handleRegisterProject(
      { workspaceRoot: workspace, log: fakeLogger() },
      {
        name: "app",
        url: origin,
      },
    );

    const content = await buildProjectsContext(workspace, fakeLogger());

    expect(content).toContain("## Registered Projects");
    expect(content).toContain("- app: main");
  });

  it("includes the dirty file count and detached state", async () => {
    const origin = await createProjectOrigin(base, "app");
    await handleRegisterProject(
      { workspaceRoot: workspace, log: fakeLogger() },
      {
        name: "app",
        url: origin,
      },
    );

    const projectPath = join(workspace, "projects", "app");
    await writeFile(join(projectPath, "one.txt"), "1\n", "utf8");
    await writeFile(join(projectPath, "two.txt"), "2\n", "utf8");
    await runGit(projectPath, ["checkout", "--detach", "HEAD"]);

    const content = await buildProjectsContext(workspace, fakeLogger());

    expect(content).toMatch(/- app: [0-9a-f]+ \(detached\) — 2 uncommitted changes/);
  });
});

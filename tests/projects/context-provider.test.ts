import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ContextProviderInput } from "../../src/extensions/api.ts";
import { runGit } from "../../src/extensions/git/git.ts";
import { createProjectsContextProvider } from "../../src/extensions/projects/context-provider.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { createProjectOrigin, createWorkspace, fakeLogger, makeTempDir } from "./helpers.ts";

const input = {} as ContextProviderInput;

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = await createWorkspace(base);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("projects context provider", () => {
  it("points at register_project when nothing is registered", async () => {
    const block = await createProjectsContextProvider(workspace, fakeLogger()).provide(input);

    expect(block).toEqual({
      tag: "projects",
      content: "No projects registered. Use register_project to add one.",
    });
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

    const block = await createProjectsContextProvider(workspace, fakeLogger()).provide(input);

    expect(block?.tag).toBe("projects");
    expect(block?.content).toContain("## Registered Projects");
    expect(block?.content).toContain("- app: main");
    expect(block?.content).not.toContain("uncommitted");
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

    const block = await createProjectsContextProvider(workspace, fakeLogger()).provide(input);

    expect(block?.content).toMatch(/- app: [0-9a-f]+ \(detached\) — 2 uncommitted changes/);
  });
});

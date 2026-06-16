import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GitApi } from "../../src/extensions/api.ts";
import { createProjectsExchangeProcessor } from "../../src/extensions/projects/processor.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { commitAllDeterministic } from "../../src/git/commit.ts";
import { runGit } from "../../src/git/git.ts";
import {
  configureIdentity,
  createProjectOrigin,
  createWorkspace,
  fakeLogger,
  headOf,
  lastSubject,
  makeTempDir,
} from "./helpers.ts";

const ctx = { userText: "hi", assistantText: "ok" };

const smartPush = vi.fn();

const git: GitApi = {
  commitAllDeterministic: (options) =>
    commitAllDeterministic({ ...options, log: options.log ?? fakeLogger() }),
  smartPush: (...args: unknown[]) => smartPush(...args),
} as unknown as GitApi;

let base: string;
let workspace: string;
let origin: string;
let projectPath: string;

beforeEach(async () => {
  vi.clearAllMocks();

  base = await makeTempDir();
  origin = await createProjectOrigin(base, "app");
  workspace = await createWorkspace(base);

  await handleRegisterProject(
    { workspaceRoot: workspace, log: fakeLogger() },
    { name: "app", url: origin },
  );

  projectPath = join(workspace, "projects", "app");
  await configureIdentity(projectPath);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("projects exchange processor", () => {
  it("commits a dirty submodule deterministically without pushing", async () => {
    await writeFile(join(projectPath, "feature.ts"), "export const x = 1;\n", "utf8");

    await createProjectsExchangeProcessor({
      workspaceRoot: workspace,
      git,
      log: fakeLogger(),
    }).process(ctx);

    expect(await lastSubject(projectPath)).toMatch(/^Update app files \(\d{4}-\d{2}-\d{2}\)$/);
    expect(await runGit(projectPath, ["status", "--porcelain"])).toBe("");
    expect(smartPush).not.toHaveBeenCalled();
  });

  it("leaves a clean submodule untouched", async () => {
    const head = await headOf(projectPath);

    await createProjectsExchangeProcessor({
      workspaceRoot: workspace,
      git,
      log: fakeLogger(),
    }).process(ctx);

    expect(await headOf(projectPath)).toBe(head);
    expect(smartPush).not.toHaveBeenCalled();
  });
});

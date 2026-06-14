import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { GitApi, PostProcessorContext } from "../../src/extensions/api.ts";
import { createProjectsProcessor } from "../../src/extensions/projects/processor.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { type Completer, commitAll } from "../../src/git/commit.ts";
import { runGit } from "../../src/git/git.ts";
import { smartPull, smartPush } from "../../src/git/sync.ts";
import {
  configureIdentity,
  createProjectOrigin,
  createWorkspace,
  fakeLogger,
  headOf,
  lastSubject,
  makeTempDir,
} from "./helpers.ts";

const context = (): PostProcessorContext => ({
  session: {} as SessionRecord,
  transcriptPath: null,
  log: fakeLogger(),
});

const git: GitApi = {
  commitAll: (options) => commitAll({ ...options, log: options.log ?? fakeLogger() }),
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
  it("commits and pushes each dirty project with the generated message", async () => {
    await writeFile(join(projectPath, "feature.ts"), "export const x = 1;\n", "utf8");
    const side: Completer = { complete: vi.fn().mockResolvedValue("Add feature module") };

    await createProjectsProcessor({ workspaceRoot: workspace, side, git }).process(context());

    expect(await lastSubject(projectPath)).toBe("Add feature module");
    expect(await runGit(projectPath, ["status", "--porcelain"])).toBe("");
    expect(await headOf(origin)).toBe(await headOf(projectPath));
  });

  it("leaves clean projects untouched", async () => {
    const side: Completer = { complete: vi.fn() };
    const head = await headOf(projectPath);

    await createProjectsProcessor({ workspaceRoot: workspace, side, git }).process(context());

    expect(side.complete).not.toHaveBeenCalled();
    expect(await headOf(projectPath)).toBe(head);
  });

  it("falls back to a deterministic message when generation fails", async () => {
    await writeFile(join(projectPath, "feature.ts"), "export const x = 1;\n", "utf8");
    const side: Completer = { complete: vi.fn().mockRejectedValue(new Error("model down")) };

    await createProjectsProcessor({ workspaceRoot: workspace, side, git }).process(context());

    expect(await lastSubject(projectPath)).toMatch(/^Update app files \(\d{4}-\d{2}-\d{2}\)$/);
    expect(await headOf(origin)).toBe(await headOf(projectPath));
  });
});

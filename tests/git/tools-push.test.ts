import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GitToolDeps } from "../../src/extensions/git/tools.ts";
import { handleCommitWorkspace } from "../../src/extensions/git/tools.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { runGit } from "../../src/git/git.ts";
import { createProjectOrigin } from "../projects/helpers.ts";
import {
  commitFile,
  configureIdentity,
  fakeLogger,
  headOf,
  initRepo,
  makeTempDir,
} from "./helpers.ts";

// Local submodule clones are blocked by default (CVE-2022-39253); the env
// allowlist is the only switch that reaches the spawned clone.
process.env.GIT_ALLOW_PROTOCOL = "file";

const side = { complete: vi.fn().mockResolvedValue("Update files") };

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, ".gitignore", ".tachikoma/\n", "Initial commit");
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

const gitDeps = (): GitToolDeps => ({ workspaceRoot: workspace, side, log: fakeLogger() });

/** Create a bare `origin`, wire the workspace to it, and push the seed commit. */
const addWorkspaceOrigin = async (): Promise<string> => {
  const origin = join(base, "workspace-origin.git");
  await runGit(base, ["init", "--bare", "-b", "main", origin]);
  await runGit(workspace, ["remote", "add", "origin", origin]);
  await runGit(workspace, ["push", "-u", "origin", "main"]);
  return origin;
};

describe("handleCommitWorkspace — push", () => {
  it("pushes the workspace to origin when ahead and reports it", async () => {
    const origin = await addWorkspaceOrigin();
    await writeFile(join(workspace, "notes.md"), "hi\n", "utf8");

    const output = await handleCommitWorkspace(gitDeps(), { message: "Add notes" });

    expect(output).toContain("Committed workspace changes: Add notes");
    expect(output).toContain("Pushed workspace to origin.");
    expect(await headOf(workspace)).toBe(await runGit(origin, ["rev-parse", "main"]));
  });

  it("does not push when push is false", async () => {
    const origin = await addWorkspaceOrigin();
    await writeFile(join(workspace, "notes.md"), "hi\n", "utf8");
    const originBefore = await runGit(origin, ["rev-parse", "main"]);

    const output = await handleCommitWorkspace(gitDeps(), { message: "Add notes", push: false });

    expect(output).toContain("Committed workspace changes: Add notes");
    expect(output).not.toContain("Pushed");
    expect(await runGit(origin, ["rev-parse", "main"])).toBe(originBefore);
  });

  it("omits a push line when the workspace is already up to date", async () => {
    await addWorkspaceOrigin();

    const output = await handleCommitWorkspace(gitDeps(), {});

    expect(output).toBe("Nothing to commit — the working tree is clean.");
  });

  it("pushes a clean-but-ahead project submodule (evidence case)", async () => {
    const appOrigin = await createProjectOrigin(base, "app");
    await handleRegisterProject(
      { workspaceRoot: workspace, log: fakeLogger() },
      { name: "app", url: appOrigin },
    );

    const appPath = join(workspace, "projects", "app");
    await configureIdentity(appPath);
    await commitFile(appPath, "feature.md", "new\n", "Add feature");
    // Record the bumped submodule pointer so the workspace tree is clean — the
    // submodule is now ahead of its own origin with a clean working tree.
    await runGit(workspace, ["add", "projects/app"]);
    await runGit(workspace, ["commit", "-m", "Bump app pointer"]);
    const appOriginBefore = await runGit(appOrigin, ["rev-parse", "main"]);

    const output = await handleCommitWorkspace(gitDeps(), {});

    expect(output).toBe(
      "Nothing to commit — the working tree is clean.\nPushed project 'app' to origin.",
    );
    expect(await runGit(appOrigin, ["rev-parse", "main"])).not.toBe(appOriginBefore);
    expect(await runGit(appOrigin, ["rev-parse", "main"])).toBe(await headOf(appPath));
  });
});

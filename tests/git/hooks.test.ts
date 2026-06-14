import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initializeWorkspaceRepo } from "../../src/extensions/git/hooks.ts";
import { runGit } from "../../src/git/git.ts";
import { smartPull } from "../../src/git/sync.ts";
import {
  commitFile,
  configureIdentity,
  fakeLogger,
  headOf,
  initRepo,
  makeTempDir,
} from "./helpers.ts";

vi.mock("../../src/git/sync.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../src/git/sync.ts")>();

  return { ...actual, smartPull: vi.fn(actual.smartPull) };
});

const smartPullMock = vi.mocked(smartPull);
const realSmartPull = smartPullMock.getMockImplementation();

const log = fakeLogger();

afterEach(() => {
  if (realSmartPull != null) smartPullMock.mockImplementation(realSmartPull);
});

/**
 * Seed a bare origin with the managed gitignore already committed (so the hook's
 * gitignore pass stays a no-op) and clone it into the workspace path.
 */
const setupOriginWorkspace = async (basePath: string, workspacePath: string): Promise<string> => {
  const origin = join(basePath, "origin.git");
  await runGit(basePath, ["init", "--bare", "-b", "main", origin]);

  const seeder = join(basePath, "seeder");
  await runGit(basePath, ["clone", origin, seeder]);
  await configureIdentity(seeder);
  await commitFile(seeder, ".gitignore", ".tachikoma/\n", "Add gitignore");
  await runGit(seeder, ["push", "-u", "origin", "main"]);

  await runGit(basePath, ["clone", origin, workspacePath]);
  await configureIdentity(workspacePath);

  return seeder;
};

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);

  vi.mocked(log.debug).mockClear();
  vi.mocked(log.info).mockClear();
  vi.mocked(log.warn).mockClear();
  smartPullMock.mockClear();
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("initializeWorkspaceRepo", () => {
  it("initializes a fresh repo with identity, initial commits, and gitignore", async () => {
    await initializeWorkspaceRepo(workspace, log);

    await expect(access(join(workspace, ".git"))).resolves.toBeUndefined();
    expect(await runGit(workspace, ["rev-list", "--count", "HEAD"])).toBe("2");
    expect(await runGit(workspace, ["config", "user.name"])).toBe("Tachikoma");
    expect(await readFile(join(workspace, ".gitignore"), "utf8")).toContain(".tachikoma/");
  });

  it("is idempotent — a second run leaves the repo untouched", async () => {
    await initializeWorkspaceRepo(workspace, log);
    const head = await headOf(workspace);

    await initializeWorkspaceRepo(workspace, log);

    expect(await headOf(workspace)).toBe(head);
    expect(await runGit(workspace, ["rev-list", "--count", "HEAD"])).toBe("2");
  });

  it("appends missing gitignore entries to an existing repo without committing", async () => {
    await initRepo(workspace);
    await writeFile(join(workspace, ".gitignore"), ".env\n", "utf8");
    await runGit(workspace, ["add", ".gitignore"]);
    await runGit(workspace, ["commit", "-m", "Custom gitignore"]);

    await initializeWorkspaceRepo(workspace, log);

    expect(await readFile(join(workspace, ".gitignore"), "utf8")).toBe(".env\n.tachikoma/\n");
    expect(await runGit(workspace, ["status", "--porcelain"])).toContain(".gitignore");
  });

  it("inserts a separator when the existing gitignore lacks a trailing newline", async () => {
    await initRepo(workspace);
    await writeFile(join(workspace, ".gitignore"), ".env", "utf8");
    await runGit(workspace, ["add", ".gitignore"]);
    await runGit(workspace, ["commit", "-m", "Custom gitignore"]);

    await initializeWorkspaceRepo(workspace, log);

    expect(await readFile(join(workspace, ".gitignore"), "utf8")).toBe(".env\n.tachikoma/\n");
  });

  it("syncs with the origin remote when one is configured", async () => {
    const origin = join(base, "origin.git");
    await runGit(base, ["init", "--bare", "-b", "main", origin]);

    const seeder = join(base, "seeder");
    await runGit(base, ["clone", origin, seeder]);
    await configureIdentity(seeder);
    // The seeded gitignore already holds the managed entries, so the hook's
    // gitignore pass stays a no-op and the sync isn't skipped as dirty.
    await commitFile(seeder, ".gitignore", ".tachikoma/\n", "Add gitignore");
    await runGit(seeder, ["push", "-u", "origin", "main"]);

    await runGit(base, ["clone", origin, workspace]);
    await configureIdentity(workspace);

    await commitFile(seeder, "remote-news.txt", "hello\n", "Remote update");
    await runGit(seeder, ["push", "origin", "main"]);

    await initializeWorkspaceRepo(workspace, log);

    await expect(access(join(workspace, "remote-news.txt"))).resolves.toBeUndefined();
    expect(await headOf(workspace)).toBe(await headOf(seeder));
  });

  it("skips the remote sync when no origin is configured", async () => {
    await initializeWorkspaceRepo(workspace, log);

    expect(smartPullMock).not.toHaveBeenCalled();
  });

  it("logs up-to-date when the workspace already matches the remote", async () => {
    await setupOriginWorkspace(base, workspace);

    await initializeWorkspaceRepo(workspace, log);

    expect(log.debug).toHaveBeenCalledWith("workspace already up to date");
  });

  it("warns and skips the sync when the workspace has uncommitted changes", async () => {
    await setupOriginWorkspace(base, workspace);
    await writeFile(join(workspace, "dirty.txt"), "dirty\n", "utf8");

    await initializeWorkspaceRepo(workspace, log);

    expect(log.warn).toHaveBeenCalledWith("workspace has uncommitted changes — skipping sync");
  });

  it("warns when the sync fails on an unresolvable divergence", async () => {
    const seeder = await setupOriginWorkspace(base, workspace);

    await commitFile(seeder, "conflict.txt", "from remote\n", "Remote conflict");
    await runGit(seeder, ["push", "origin", "main"]);
    await commitFile(workspace, "conflict.txt", "from local\n", "Local conflict");

    await initializeWorkspaceRepo(workspace, log);

    expect(log.warn).toHaveBeenCalledWith("workspace sync failed — continuing with local state");
  });

  it("logs the result for a successful non-trivial sync", async () => {
    const seeder = await setupOriginWorkspace(base, workspace);

    await commitFile(seeder, "remote-news.txt", "hello\n", "Remote update");
    await runGit(seeder, ["push", "origin", "main"]);

    await initializeWorkspaceRepo(workspace, log);

    expect(log.info).toHaveBeenCalledWith(
      expect.objectContaining({ result: expect.any(String) }),
      "workspace synced",
    );
  });

  it("swallows an unexpected sync error and warns", async () => {
    await setupOriginWorkspace(base, workspace);
    smartPullMock.mockRejectedValueOnce(new Error("boom"));

    await initializeWorkspaceRepo(workspace, log);

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ err: expect.any(Error) }),
      "workspace sync failed",
    );
  });
});

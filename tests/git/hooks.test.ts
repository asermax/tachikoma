import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { runGit } from "../../src/extensions/git/git.ts";
import { initializeWorkspaceRepo } from "../../src/extensions/git/hooks.ts";
import {
  commitFile,
  configureIdentity,
  fakeLogger,
  headOf,
  initRepo,
  makeTempDir,
} from "./helpers.ts";

const log = fakeLogger();

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);
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
});

import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { commitAll, commitAllDeterministic } from "../../src/git/commit.ts";
import type { CommitAgent } from "../../src/git/commit-agent.ts";
import { runGit } from "../../src/git/git.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir, subjects } from "./helpers.ts";

let base: string;
let workspace: string;

beforeEach(async () => {
  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, "seed.txt", "seed\n", "Seed commit");
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("commitAll", () => {
  it("returns [] without running the agent when the tree is clean", async () => {
    let called = false;
    const agent: CommitAgent = async () => {
      called = true;
    };

    const result = await commitAll({
      agent,
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toEqual([]);
    expect(called).toBe(false);
  });

  it("runs the agent first and returns the subjects it committed", async () => {
    await writeFile(join(workspace, "notes.md"), "draft\n", "utf8");
    const agent: CommitAgent = async (cwd) => {
      await runGit(cwd, ["add", "-A"]);
      await runGit(cwd, ["commit", "-m", "Add notes"]);
    };

    const result = await commitAll({
      agent,
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toEqual(["Add notes"]);
    expect(await subjects(workspace)).toEqual(["Seed commit", "Add notes"]);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("returns every subject when the agent makes multiple grouped commits", async () => {
    await writeFile(join(workspace, "notes.md"), "a\n", "utf8");
    await writeFile(join(workspace, "config.toml"), "x\n", "utf8");
    const agent: CommitAgent = async (cwd) => {
      await runGit(cwd, ["add", "notes.md"]);
      await runGit(cwd, ["commit", "-m", "Add memory"]);
      await runGit(cwd, ["add", "config.toml"]);
      await runGit(cwd, ["commit", "-m", "Update config"]);
    };

    const result = await commitAll({
      agent,
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toEqual(["Add memory", "Update config"]);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("falls back to a single deterministic commit when the agent throws", async () => {
    await writeFile(join(workspace, "notes.md"), "draft\n", "utf8");
    const agent: CommitAgent = async () => {
      throw new Error("agent down");
    };
    const log = fakeLogger();

    const result = await commitAll({
      agent,
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log,
    });

    expect(result).toEqual(["Snapshot"]);
    expect(await subjects(workspace)).toEqual(["Seed commit", "Snapshot"]);
    expect(log.warn).toHaveBeenCalledWith(
      expect.anything(),
      expect.stringContaining("agent failed"),
    );
  });

  it("commits the remainder when the agent leaves the tree dirty", async () => {
    await writeFile(join(workspace, "a.md"), "a\n", "utf8");
    await writeFile(join(workspace, "b.md"), "b\n", "utf8");
    const agent: CommitAgent = async (cwd) => {
      await runGit(cwd, ["add", "a.md"]);
      await runGit(cwd, ["commit", "-m", "Add a"]);
      // b.md intentionally left uncommitted
    };
    const log = fakeLogger();

    const result = await commitAll({
      agent,
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log,
    });

    expect(result).toEqual(["Add a", "Snapshot"]);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
    expect(log.warn).toHaveBeenCalledWith(expect.anything(), expect.stringContaining("dirty"));
  });

  it("still returns the agent's subjects when it committed everything before throwing", async () => {
    await writeFile(join(workspace, "notes.md"), "draft\n", "utf8");
    const agent: CommitAgent = async (cwd) => {
      await runGit(cwd, ["add", "-A"]);
      await runGit(cwd, ["commit", "-m", "Add notes"]);
      throw new Error("failed during verification");
    };

    const result = await commitAll({
      agent,
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toEqual(["Add notes"]);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });
});

describe("commitAllDeterministic", () => {
  it("returns [] when the tree is clean", async () => {
    const result = await commitAllDeterministic({
      cwd: workspace,
      message: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toEqual([]);
  });

  it("commits everything in one commit with the given message", async () => {
    await writeFile(join(workspace, "a.md"), "a\n", "utf8");
    await writeFile(join(workspace, "b.md"), "b\n", "utf8");

    const result = await commitAllDeterministic({
      cwd: workspace,
      message: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toEqual(["Snapshot"]);
    expect(await subjects(workspace)).toEqual(["Seed commit", "Snapshot"]);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });
});

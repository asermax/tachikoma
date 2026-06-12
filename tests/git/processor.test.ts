import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { PostProcessorContext } from "../../src/extensions/api.ts";
import type { Completer } from "../../src/extensions/git/commit.ts";
import { runGit } from "../../src/extensions/git/git.ts";
import { createGitProcessor } from "../../src/extensions/git/processor.ts";
import { commitFile, fakeLogger, headOf, initRepo, lastSubject, makeTempDir } from "./helpers.ts";

const context = (): PostProcessorContext => ({
  session: {} as SessionRecord,
  transcriptPath: null,
  log: fakeLogger(),
});

const completerReturning = (message: string): Completer => ({
  complete: vi.fn().mockResolvedValue(message),
});

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

describe("git processor", () => {
  it("stages and commits all changes with the generated message", async () => {
    await writeFile(join(workspace, "notes.md"), "remember this\n", "utf8");
    const side = completerReturning("Add session notes");

    await createGitProcessor({ workspaceRoot: workspace, side }).process(context());

    expect(await lastSubject(workspace)).toBe("Add session notes");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
    expect(side.complete).toHaveBeenCalledWith(
      expect.objectContaining({ user: expect.stringContaining("notes.md") }),
    );
  });

  it("does nothing when the workspace is clean", async () => {
    const side = completerReturning("unused");
    const head = await headOf(workspace);

    await createGitProcessor({ workspaceRoot: workspace, side }).process(context());

    expect(side.complete).not.toHaveBeenCalled();
    expect(await headOf(workspace)).toBe(head);
  });

  it("falls back to a deterministic message when generation fails", async () => {
    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const side: Completer = { complete: vi.fn().mockRejectedValue(new Error("model down")) };

    await createGitProcessor({ workspaceRoot: workspace, side }).process(context());

    expect(await lastSubject(workspace)).toMatch(/^Update workspace files \(\d{4}-\d{2}-\d{2}\)$/);
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });

  it("pushes to origin after committing when a remote is configured", async () => {
    const origin = join(base, "origin.git");
    await runGit(base, ["init", "--bare", "-b", "main", origin]);
    await runGit(workspace, ["remote", "add", "origin", origin]);
    await runGit(workspace, ["push", "-u", "origin", "main"]);

    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Add notes"),
    }).process(context());

    expect(await headOf(origin)).toBe(await headOf(workspace));
  });
});

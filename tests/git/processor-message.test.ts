import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PostProcessorContext } from "../../src/extensions/api.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir } from "./helpers.ts";

const commitAll = vi.hoisted(() => vi.fn());

vi.mock("../../src/git/commit.ts", () => ({ commitAll }));

const { createGitProcessor } = await import("../../src/extensions/git/processor.ts");

const context = (log: PostProcessorContext["log"]): PostProcessorContext => ({
  trunk: null,
  transcriptPath: null,
  log,
});

let base: string;
let workspace: string;

beforeEach(async () => {
  commitAll.mockReset();

  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, "seed.txt", "seed\n", "Seed commit");
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("git processor commit message logging", () => {
  it("does not log a commit when the commit pass yields no subjects", async () => {
    commitAll.mockResolvedValue([]);
    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const log = fakeLogger();

    await createGitProcessor({
      workspaceRoot: workspace,
      agent: async () => {},
    }).process(context(log));

    expect(log.info).not.toHaveBeenCalledWith(
      expect.objectContaining({ subjects: expect.anything() }),
      expect.stringContaining("committed workspace changes"),
    );
  });
});

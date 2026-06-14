import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { PostProcessorContext } from "../../src/extensions/api.ts";
import type { Completer } from "../../src/extensions/git/commit.ts";
import { runGit } from "../../src/extensions/git/git.ts";
import { PUSH_RESULT } from "../../src/extensions/git/sync.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir } from "./helpers.ts";

const smartPush = vi.hoisted(() => vi.fn());

vi.mock("../../src/extensions/git/sync.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/extensions/git/sync.ts")>()),
  smartPush,
}));

const { createGitProcessor } = await import("../../src/extensions/git/processor.ts");

const context = (log: PostProcessorContext["log"]): PostProcessorContext => ({
  session: {} as SessionRecord,
  transcriptPath: null,
  log,
});

const completerReturning = (message: string): Completer => ({
  complete: vi.fn().mockResolvedValue(message),
});

let base: string;
let workspace: string;

beforeEach(async () => {
  smartPush.mockReset();

  base = await makeTempDir();
  workspace = join(base, "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, "seed.txt", "seed\n", "Seed commit");

  const origin = join(base, "origin.git");
  await runGit(base, ["init", "--bare", "-b", "main", origin]);
  await runGit(workspace, ["remote", "add", "origin", origin]);
  await runGit(workspace, ["push", "-u", "origin", "main"]);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("git processor push outcomes", () => {
  it("logs at debug when there is nothing to push", async () => {
    smartPush.mockResolvedValue(PUSH_RESULT.nothingToPush);
    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const log = fakeLogger();

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Add notes"),
    }).process(context(log));

    expect(smartPush).toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith("nothing to push");
  });

  it("logs success when the push lands", async () => {
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);
    await writeFile(join(workspace, "notes.md"), "content\n", "utf8");
    const log = fakeLogger();

    await createGitProcessor({
      workspaceRoot: workspace,
      side: completerReturning("Add notes"),
    }).process(context(log));

    expect(log.info).toHaveBeenCalledWith(
      expect.objectContaining({ result: PUSH_RESULT.pushed }),
      expect.stringContaining("pushed"),
    );
  });
});

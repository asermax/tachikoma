import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createGitExchangeProcessor } from "../../src/extensions/git/exchange.ts";
import { runGit } from "../../src/git/git.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir, recordingDebouncer } from "./helpers.ts";

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

describe("git exchange processor", () => {
  it("resets the debounce timer on each exchange and does not commit", async () => {
    await writeFile(join(workspace, "notes.md"), "remember this\n", "utf8");
    const debouncer = recordingDebouncer();

    await createGitExchangeProcessor({ debouncer, log: fakeLogger() }).process({ userText: "hi" });

    expect(debouncer.touch).toHaveBeenCalledTimes(1);
    // Nothing is committed on the exchange path — the file stays uncommitted.
    expect(await runGit(workspace, ["status", "--porcelain"])).not.toBe("");
  });

  it("resets the timer on every exchange, not just the first", async () => {
    const debouncer = recordingDebouncer();

    const processor = createGitExchangeProcessor({ debouncer, log: fakeLogger() });
    await processor.process({ userText: "one" });
    await processor.process({ userText: "two" });

    expect(debouncer.touch).toHaveBeenCalledTimes(2);
  });
});

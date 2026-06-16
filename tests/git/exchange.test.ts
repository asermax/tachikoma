import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createGitExchangeProcessor } from "../../src/extensions/git/exchange.ts";
import { runGit } from "../../src/git/git.ts";
import {
  agentCommittingAs,
  commitFile,
  fakeLogger,
  headOf,
  initRepo,
  lastSubject,
  makeTempDir,
  recordingAgentCommittingAs,
} from "./helpers.ts";

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
  it("makes an agent-grouped commit when the workspace has changes", async () => {
    await writeFile(join(workspace, "notes.md"), "remember this\n", "utf8");
    const head = await headOf(workspace);

    const processor = createGitExchangeProcessor({
      workspaceRoot: workspace,
      agent: agentCommittingAs("Add notes"),
      log: fakeLogger(),
    });
    await processor.process({ userText: "hi", assistantText: "ok" });
    await processor.whenIdle();

    expect(await lastSubject(workspace)).toBe("Add notes");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");

    const newCommits = await runGit(workspace, ["rev-list", `${head}..HEAD`, "--count"]);
    expect(newCommits).toBe("1");
  });

  it("does nothing when the workspace is clean", async () => {
    const head = await headOf(workspace);
    const recording = recordingAgentCommittingAs("Add notes");

    const processor = createGitExchangeProcessor({
      workspaceRoot: workspace,
      agent: recording.agent,
      log: fakeLogger(),
    });
    await processor.process({ userText: "hi", assistantText: "ok" });
    await processor.whenIdle();

    expect(await headOf(workspace)).toBe(head);
    expect(recording.calls).toBe(0);
  });

  it("single-flights overlapping exchanges — a second call while one is in flight is a no-op", async () => {
    await writeFile(join(workspace, "notes.md"), "remember this\n", "utf8");
    const recording = recordingAgentCommittingAs("Add notes");

    const processor = createGitExchangeProcessor({
      workspaceRoot: workspace,
      agent: recording.agent,
      log: fakeLogger(),
    });

    await processor.process({ userText: "hi", assistantText: "ok" });
    await processor.process({ userText: "again", assistantText: "ok" });
    await processor.whenIdle();

    expect(recording.calls).toBe(1);
  });
});

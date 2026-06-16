import { rm } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HeadlessRunResult } from "../../src/agent/side-run.ts";
import { createCommitAgent } from "../../src/git/commit-agent.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir } from "./helpers.ts";

const log = fakeLogger();

let base: string;

beforeEach(async () => {
  base = await makeTempDir();
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("createCommitAgent", () => {
  it("runs a side agent once with cwd-scoped custom tools at the processor tier", async () => {
    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "done" }));
    const agent = createCommitAgent({ run }, "workspace");

    await agent("/some/repo", log);

    expect(run).toHaveBeenCalledTimes(1);

    const options = run.mock.calls[0]?.[0];
    expect(options?.system).toContain("cohesive");
    expect(options?.prompt).toContain("workspace");
    expect(options?.tier).toBe("processor");

    const toolNames = options?.customTools?.map((tool) => tool.name) ?? [];
    expect(toolNames).toEqual(["read_file", "git"]);
  });

  it("uses the project prompt in project mode", async () => {
    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "done" }));
    const agent = createCommitAgent({ run }, "project");

    await agent("/some/repo", log);

    const options = run.mock.calls[0]?.[0];
    expect(options?.system).toContain("commit-message style");
    expect(options?.system).toContain("CONTRIBUTING.md");
    expect(options?.prompt).toContain("project");
  });

  it("its tools read files and run git against the scoped repo", async () => {
    await initRepo(base);
    await commitFile(base, "file.txt", "original\n", "seed");

    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "" }));
    await createCommitAgent({ run }, "workspace")(base, log);

    const tools = run.mock.calls[0]?.[0].customTools ?? [];
    const readFile = tools.find((tool) => tool.name === "read_file");
    const git = tools.find((tool) => tool.name === "git");

    expect((await readFile?.execute("id", { path: "file.txt" }))?.content[0]?.text).toContain(
      "original",
    );

    expect(
      (await git?.execute("id", { args: ["status", "--porcelain"] }))?.content[0]?.text,
    ).toContain("exit 0");
  });

  it("its git tool accepts an absolute path verbatim for reads", async () => {
    await initRepo(base);

    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "" }));
    await createCommitAgent({ run }, "workspace")(base, log);

    const tools = run.mock.calls[0]?.[0].customTools ?? [];
    const readFile = tools.find((tool) => tool.name === "read_file");

    const absolute = join(base, "absolute.txt");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(absolute, "by absolute path\n", "utf8");

    expect((await readFile?.execute("id", { path: absolute }))?.content[0]?.text).toContain(
      "by absolute path",
    );
  });

  it("its git tool allows status, diff, log, add, commit, and show", async () => {
    await initRepo(base);

    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "" }));
    await createCommitAgent({ run }, "workspace")(base, log);

    const git = (run.mock.calls[0]?.[0].customTools ?? []).find((tool) => tool.name === "git");

    for (const allowed of ["status", "diff", "log", "add", "commit", "show"]) {
      const result = await git?.execute("id", { args: [allowed] });
      expect(result?.content[0]?.text).not.toContain("Refused");
    }
  });

  it("its git tool refuses destructive operations", async () => {
    await initRepo(base);

    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "" }));
    await createCommitAgent({ run }, "workspace")(base, log);

    const git = (run.mock.calls[0]?.[0].customTools ?? []).find((tool) => tool.name === "git");

    for (const forbidden of [
      "push",
      "reset",
      "rebase",
      "clean",
      "remote",
      "filter-repo",
      "checkout",
      "fetch",
    ]) {
      const result = await git?.execute("id", { args: [forbidden, "x"] });
      expect(result?.content[0]?.text).toContain("Refused");
    }
  });

  it("propagates agent failures so the caller can fall back", async () => {
    const run = vi.fn(async (): Promise<HeadlessRunResult> => {
      throw new Error("agent blew up");
    });

    await expect(createCommitAgent({ run }, "workspace")("/repo", log)).rejects.toThrow("blew up");
  });
});

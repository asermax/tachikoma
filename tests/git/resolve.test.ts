import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HeadlessRunResult } from "../../src/agent/side-run.ts";
import { createGitResolver } from "../../src/extensions/git/resolve.ts";
import { commitFile, fakeLogger, initRepo, makeTempDir } from "./helpers.ts";

const log = fakeLogger();

let base: string;

beforeEach(async () => {
  base = await makeTempDir();
});

afterEach(async () => {
  const { rm } = await import("node:fs/promises");
  await rm(base, { recursive: true, force: true });
});

describe("createGitResolver", () => {
  it("runs a side agent with cwd-scoped custom tools", async () => {
    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "done" }));
    const resolver = createGitResolver({ run });

    await resolver("/some/repo", "origin/main", log);

    expect(run).toHaveBeenCalledTimes(1);

    const options = run.mock.calls[0]?.[0];
    expect(options?.system).toContain("rebase");
    expect(options?.prompt).toContain("origin/main");

    const toolNames = options?.customTools?.map((tool) => tool.name) ?? [];
    expect(toolNames).toEqual(["read_conflict", "write_resolved", "git"]);
  });

  it("its tools read, write, and run git against the scoped repo", async () => {
    await initRepo(base);
    await commitFile(base, "file.txt", "original\n", "seed");

    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "" }));
    const resolver = createGitResolver({ run });

    await resolver(base, "origin/main", log);

    const tools = run.mock.calls[0]?.[0].customTools ?? [];
    const read = tools.find((tool) => tool.name === "read_conflict");
    const write = tools.find((tool) => tool.name === "write_resolved");
    const git = tools.find((tool) => tool.name === "git");

    expect((await read?.execute("id", { path: "file.txt" }))?.content[0]?.text).toContain(
      "original",
    );

    await write?.execute("id", { path: "file.txt", content: "rewritten\n" });
    expect(await readFile(join(base, "file.txt"), "utf8")).toBe("rewritten\n");

    expect(
      (await git?.execute("id", { args: ["status", "--porcelain"] }))?.content[0]?.text,
    ).toContain("file.txt");
  });

  it("its git tool refuses push, fetch, reset, and remote operations", async () => {
    await initRepo(base);

    const run = vi.fn(async (): Promise<HeadlessRunResult> => ({ text: "" }));
    const resolver = createGitResolver({ run });

    await resolver(base, "origin/main", log);

    const git = (run.mock.calls[0]?.[0].customTools ?? []).find((tool) => tool.name === "git");

    for (const forbidden of ["push", "fetch", "reset", "remote"]) {
      const result = await git?.execute("id", { args: [forbidden, "x"] });
      expect(result?.content[0]?.text).toContain("Refused");
    }
  });

  it("swallows agent failures so a sync is never aborted by a thrown resolver", async () => {
    const run = vi.fn(async (): Promise<HeadlessRunResult> => {
      throw new Error("agent blew up");
    });

    await expect(createGitResolver({ run })("/repo", "origin/main", log)).resolves.toBeUndefined();
  });
});

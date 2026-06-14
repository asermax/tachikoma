import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type Completer, commitAll, generateCommitMessage } from "../../src/git/commit.ts";
import { runGit } from "../../src/git/git.ts";
import { commitFile, fakeLogger, initRepo, lastSubject, makeTempDir } from "./helpers.ts";

const completerReturning = (message: string): Completer => ({
  complete: vi.fn().mockResolvedValue(message),
});

const completerThrowing = (): Completer => ({
  complete: vi.fn().mockRejectedValue(new Error("side run failed")),
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

describe("generateCommitMessage", () => {
  it("uses the sanitized generated message when one is produced", async () => {
    const side = completerReturning('  "Add session notes"  ');

    const message = await generateCommitMessage(side, "diffstat", "fallback", fakeLogger());

    expect(message).toBe("Add session notes");
  });

  it("picks the first non-empty line and strips surrounding quotes", async () => {
    const side = completerReturning("\n\n  `Refactor loop`  \nsecond line");

    const message = await generateCommitMessage(side, "diffstat", "fallback", fakeLogger());

    expect(message).toBe("Refactor loop");
  });

  it("truncates an over-long message with an ellipsis", async () => {
    const long = "a".repeat(150);
    const side = completerReturning(long);

    const message = await generateCommitMessage(side, "diffstat", "fallback", fakeLogger());

    expect(message).toBe(`${"a".repeat(100)}…`);
  });

  it("falls back when the generated message is only whitespace", async () => {
    const side = completerReturning("   \n   ");

    const message = await generateCommitMessage(side, "diffstat", "fallback", fakeLogger());

    expect(message).toBe("fallback");
  });

  it("falls back when the generated message is only quote characters", async () => {
    const side = completerReturning('"""');

    const message = await generateCommitMessage(side, "diffstat", "fallback", fakeLogger());

    expect(message).toBe("fallback");
  });

  it("falls back and warns when generation throws", async () => {
    const log = fakeLogger();
    const side = completerThrowing();

    const message = await generateCommitMessage(side, "diffstat", "fallback", log);

    expect(message).toBe("fallback");
    expect(log.warn).toHaveBeenCalled();
  });
});

describe("commitAll", () => {
  it("returns null when there is nothing staged", async () => {
    const result = await commitAll({
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toBeNull();
    expect(await lastSubject(workspace)).toBe("Seed commit");
  });

  it("commits with an explicit message, skipping generation", async () => {
    await writeFile(join(workspace, "notes.md"), "draft\n", "utf8");
    const side = completerReturning("generated should be ignored");

    const result = await commitAll({
      cwd: workspace,
      side,
      fallbackMessage: "Snapshot",
      message: "Explicit message",
      log: fakeLogger(),
    });

    expect(result).toBe("Explicit message");
    expect(await lastSubject(workspace)).toBe("Explicit message");
    expect(side.complete).not.toHaveBeenCalled();
  });

  it("generates the message from the diffstat when a completer is supplied", async () => {
    await writeFile(join(workspace, "notes.md"), "draft\n", "utf8");
    const side = completerReturning("Add notes");

    const result = await commitAll({
      cwd: workspace,
      side,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toBe("Add notes");
    expect(await lastSubject(workspace)).toBe("Add notes");
    expect(side.complete).toHaveBeenCalledWith(
      expect.objectContaining({ user: expect.stringContaining("notes.md") }),
    );
  });

  it("uses the fallback message when no completer is supplied", async () => {
    await writeFile(join(workspace, "notes.md"), "draft\n", "utf8");

    const result = await commitAll({
      cwd: workspace,
      fallbackMessage: "Snapshot",
      log: fakeLogger(),
    });

    expect(result).toBe("Snapshot");
    expect(await lastSubject(workspace)).toBe("Snapshot");
    expect(await runGit(workspace, ["status", "--porcelain"])).toBe("");
  });
});

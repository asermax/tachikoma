import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import {
  createExtractionProcessor,
  MEMORY_FILE_TOOLS,
  type Runner,
} from "../../src/extensions/memory/extraction.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const session = { id: 1 } as SessionRecord;

const fakeRunner = () => {
  const run = vi.fn().mockResolvedValue({ text: "done" });
  const side: Runner = { run };
  return { side, run };
};

const writeTranscript = async (path: string): Promise<void> => {
  const lines = [
    { type: "session", version: 3, id: "sess-1", timestamp: "2026-06-12T10:00:00Z", cwd: "/w" },
    {
      type: "message",
      id: "1",
      parentId: null,
      message: { role: "user", content: "I moved to Berlin last month", timestamp: 1 },
    },
    {
      type: "message",
      id: "2",
      parentId: "1",
      message: { role: "assistant", content: [{ type: "text", text: "Noted — congrats!" }] },
    },
  ];

  await writeFile(path, lines.map((entry) => JSON.stringify(entry)).join("\n"), "utf8");
};

let workspace: string;
let transcriptPath: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
  transcriptPath = join(workspace, "session.jsonl");
  await writeTranscript(transcriptPath);
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("memory extraction processors", () => {
  it("runs a headless extraction over the rendered conversation", async () => {
    const { side, run } = fakeRunner();
    const processor = createExtractionProcessor("facts", {
      side,
      workspaceRoot: workspace,
      maxTranscriptChars: 10_000,
    });

    expect(processor.name).toBe("memory-facts");
    expect(processor.phase).toBe("main");

    await processor.process({ session, transcriptPath, log: fakeLog });

    expect(run).toHaveBeenCalledTimes(1);
    const options = run.mock.calls[0]?.[0];
    expect(options.tools).toEqual(MEMORY_FILE_TOOLS);
    expect(options.tier).toBe("processor");
    expect(options.system).toContain(join(workspace, "memories", "facts"));
    expect(options.system).toContain("memory extraction agent");
    expect(options.prompt).toContain("user: I moved to Berlin last month");
    expect(options.prompt).toContain("assistant: Noted — congrats!");
  });

  it("targets the store directory of each store", async () => {
    const { side, run } = fakeRunner();
    const deps = { side, workspaceRoot: workspace, maxTranscriptChars: 10_000 };

    await createExtractionProcessor("preferences", deps).process({
      session,
      transcriptPath,
      log: fakeLog,
    });
    await createExtractionProcessor("episodic", deps).process({
      session,
      transcriptPath,
      log: fakeLog,
    });

    expect(run.mock.calls[0]?.[0].system).toContain(join(workspace, "memories", "preferences"));
    expect(run.mock.calls[1]?.[0].system).toContain(join(workspace, "memories", "episodic"));
    expect(run.mock.calls[1]?.[0].system).toContain("Today's date is");
  });

  it("skips when there is no transcript", async () => {
    const { side, run } = fakeRunner();

    await createExtractionProcessor("facts", {
      side,
      workspaceRoot: workspace,
      maxTranscriptChars: 10_000,
    }).process({ session, transcriptPath: null, log: fakeLog });

    expect(run).not.toHaveBeenCalled();
  });

  it("skips when the transcript renders to an empty conversation", async () => {
    const headerOnly = join(workspace, "empty.jsonl");
    await writeFile(headerOnly, JSON.stringify({ type: "session", id: "s" }), "utf8");

    const { side, run } = fakeRunner();

    await createExtractionProcessor("facts", {
      side,
      workspaceRoot: workspace,
      maxTranscriptChars: 10_000,
    }).process({ session, transcriptPath: headerOnly, log: fakeLog });

    expect(run).not.toHaveBeenCalled();
  });

  it("sweeps files the extraction agent emptied", async () => {
    const factsDir = join(workspace, "memories", "facts");
    await mkdir(factsDir, { recursive: true });
    await writeFile(join(factsDir, "kept.md"), "still useful", "utf8");

    const run = vi.fn().mockImplementation(async () => {
      await writeFile(join(factsDir, "merged-away.md"), "", "utf8");
      return { text: "done" };
    });

    await createExtractionProcessor("facts", {
      side: { run },
      workspaceRoot: workspace,
      maxTranscriptChars: 10_000,
    }).process({ session, transcriptPath, log: fakeLog });

    expect((await readdir(factsDir)).sort()).toEqual(["kept.md"]);
  });
});

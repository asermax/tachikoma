import { mkdir, mkdtemp, readdir, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import {
  createTranscriptArchiveProcessor,
  pruneTranscripts,
} from "../../src/extensions/memory/archive.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const session = { id: 1 } as SessionRecord;

let workspace: string;

beforeEach(async () => {
  vi.clearAllMocks();
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("transcript archive processor", () => {
  it("copies the transcript named after the pi session id", async () => {
    const content = `${JSON.stringify({ type: "session", id: "sess-abc", cwd: "/w" })}\n${JSON.stringify({ type: "message", id: "1", parentId: null })}\n`;
    const src = join(workspace, "20260612_xyz.jsonl");
    await writeFile(src, content, "utf8");

    const processor = createTranscriptArchiveProcessor(workspace);
    expect(processor.phase).toBe("finalize");

    await processor.process({ session, transcriptPath: src, log: fakeLog });

    const dest = join(workspace, "memories", "transcripts", "sess-abc.jsonl");
    expect(await readFile(dest, "utf8")).toBe(content);
  });

  it("falls back to the source filename when the header has no session id", async () => {
    const src = join(workspace, "20260612_fallback.jsonl");
    await writeFile(src, `${JSON.stringify({ type: "message", id: "1" })}\n`, "utf8");

    await createTranscriptArchiveProcessor(workspace).process({
      session,
      transcriptPath: src,
      log: fakeLog,
    });

    const dest = join(workspace, "memories", "transcripts", "20260612_fallback.jsonl");
    await expect(readFile(dest, "utf8")).resolves.toContain('"id":"1"');
  });

  it("never throws when the source transcript is missing", async () => {
    await expect(
      createTranscriptArchiveProcessor(workspace).process({
        session,
        transcriptPath: join(workspace, "missing.jsonl"),
        log: fakeLog,
      }),
    ).resolves.toBeUndefined();

    expect(fakeLog.warn).toHaveBeenCalled();
  });

  it("skips silently when there is no transcript", async () => {
    await createTranscriptArchiveProcessor(workspace).process({
      session,
      transcriptPath: null,
      log: fakeLog,
    });

    expect(fakeLog.debug).toHaveBeenCalled();
  });
});

describe("pruneTranscripts", () => {
  const NOW = new Date("2026-06-12T00:00:00.000Z");
  const DAY = 24 * 60 * 60 * 1000;
  const now = () => NOW;

  const transcripts = () => join(workspace, "memories", "transcripts");

  const write = async (name: string, ageDays: number): Promise<void> => {
    const path = join(transcripts(), name);
    await writeFile(path, "{}\n", "utf8");

    const stamp = new Date(NOW.getTime() - ageDays * DAY);
    await utimes(path, stamp, stamp);
  };

  beforeEach(async () => {
    await mkdir(transcripts(), { recursive: true });
  });

  it("deletes only transcripts strictly older than the window, keeping the boundary", async () => {
    await write("old.jsonl", 91);
    await write("boundary.jsonl", 90);
    await write("recent.jsonl", 10);

    await pruneTranscripts(workspace, 90, fakeLog, now);

    expect((await readdir(transcripts())).sort()).toEqual(["boundary.jsonl", "recent.jsonl"]);
  });

  it("decides by mtime, not by a date-shaped filename", async () => {
    await write("2020-01-01.jsonl", 5);
    await write("sess-fresh.jsonl", 200);

    await pruneTranscripts(workspace, 90, fakeLog, now);

    expect(await readdir(transcripts())).toEqual(["2020-01-01.jsonl"]);
  });

  it("never deletes non-.jsonl files regardless of age", async () => {
    await write("old.jsonl", 200);
    await write("notes.txt", 200);
    await write("data.json", 200);

    await pruneTranscripts(workspace, 90, fakeLog, now);

    expect((await readdir(transcripts())).sort()).toEqual(["data.json", "notes.txt"]);
  });

  it("retains everything when retention is 0 (disabled)", async () => {
    await write("old.jsonl", 500);

    await pruneTranscripts(workspace, 0, fakeLog, now);

    expect(await readdir(transcripts())).toEqual(["old.jsonl"]);
  });

  it("is a silent no-op when the transcripts directory does not exist", async () => {
    await rm(transcripts(), { recursive: true, force: true });

    await expect(pruneTranscripts(workspace, 90, fakeLog, now)).resolves.toBeUndefined();
    expect(fakeLog.warn).not.toHaveBeenCalled();
  });

  it("warns and continues when one entry cannot be removed", async () => {
    await write("good.jsonl", 200);

    // A directory named like a transcript: stat sees it as old, unlink fails on it.
    const blocked = join(transcripts(), "blocked.jsonl");
    await mkdir(blocked);
    const stamp = new Date(NOW.getTime() - 200 * DAY);
    await utimes(blocked, stamp, stamp);

    await pruneTranscripts(workspace, 90, fakeLog, now);

    expect(await readdir(transcripts())).toEqual(["blocked.jsonl"]);
    expect(fakeLog.warn).toHaveBeenCalled();
  });
});

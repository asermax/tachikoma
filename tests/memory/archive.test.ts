import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import { createTranscriptArchiveProcessor } from "../../src/extensions/memory/archive.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const session = { id: 1 } as SessionRecord;

let workspace: string;

beforeEach(async () => {
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

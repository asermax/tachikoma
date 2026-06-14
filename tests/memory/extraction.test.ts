import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import {
  createExtractionProcessor,
  type Forker,
  MEMORY_FILE_TOOLS,
  storeInstruction,
} from "../../src/extensions/memory/extraction.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const session = { id: 1 } as SessionRecord;

const fakeForker = () => {
  const forkAndContinue = vi.fn().mockResolvedValue(undefined);
  const agent: Forker = { forkAndContinue };
  return { agent, forkAndContinue };
};

let workspace: string;
const transcriptPath = "/sessions/sess-1.jsonl";

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("memory extraction processors", () => {
  it("forks the just-ended conversation and continues it with one follow-up instruction", async () => {
    const { agent, forkAndContinue } = fakeForker();
    const processor = createExtractionProcessor("facts", { agent, workspaceRoot: workspace });

    expect(processor.name).toBe("memory-facts");
    expect(processor.phase).toBe("main");

    await processor.process({ session, transcriptPath, log: fakeLog });

    expect(forkAndContinue).toHaveBeenCalledTimes(1);
    const [source, prompt, tier, tools] = forkAndContinue.mock.calls[0] ?? [];
    // Forks from the session's own transcript — never replays it as text.
    expect(source).toBe(transcriptPath);
    expect(tier).toBe("processor");
    // Hard-limited to file tools even though the fork reuses the live session.
    expect(tools).toEqual(MEMORY_FILE_TOOLS);
    // The prompt is a follow-up user instruction to the same assistant, not a persona reset.
    expect(prompt).toContain("We just finished the conversation above");
    expect(prompt).not.toContain("<conversation>");
    expect(prompt).toContain(join(workspace, "memories", "facts"));
  });

  it("targets the store directory and store-specific instruction for each store", async () => {
    const { agent, forkAndContinue } = fakeForker();
    const deps = { agent, workspaceRoot: workspace };

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

    expect(forkAndContinue.mock.calls[0]?.[1]).toContain(
      join(workspace, "memories", "preferences"),
    );
    expect(forkAndContinue.mock.calls[1]?.[1]).toContain(join(workspace, "memories", "episodic"));
    expect(forkAndContinue.mock.calls[1]?.[1]).toContain("Today's date is");
  });

  it("instructs the fork to run as a silent background step", () => {
    const instruction = storeInstruction("facts", workspace);

    expect(instruction).toContain("SILENT background memory-maintenance step");
    expect(instruction).toContain("Do NOT");
  });

  it("skips when there is no transcript", async () => {
    const { agent, forkAndContinue } = fakeForker();

    await createExtractionProcessor("facts", { agent, workspaceRoot: workspace }).process({
      session,
      transcriptPath: null,
      log: fakeLog,
    });

    expect(forkAndContinue).not.toHaveBeenCalled();
  });

  it("sweeps files the forked agent emptied", async () => {
    const factsDir = join(workspace, "memories", "facts");
    await mkdir(factsDir, { recursive: true });
    await writeFile(join(factsDir, "kept.md"), "still useful", "utf8");

    const forkAndContinue = vi.fn().mockImplementation(async () => {
      await writeFile(join(factsDir, "merged-away.md"), "", "utf8");
    });

    await createExtractionProcessor("facts", {
      agent: { forkAndContinue },
      workspaceRoot: workspace,
    }).process({ session, transcriptPath, log: fakeLog });

    expect((await readdir(factsDir)).sort()).toEqual(["kept.md"]);
  });
});

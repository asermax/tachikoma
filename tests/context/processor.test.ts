import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SideRunner } from "../../src/agent/side-run.ts";
import type { SessionRecord } from "../../src/db/core-schema.ts";
import {
  cleanPendingSignals,
  createCoreContextProcessor,
  PENDING_SIGNALS_FILENAME,
  parsePendingSignals,
} from "../../src/extensions/context/processor.ts";
import { localIsoDate } from "../../src/extensions/memory/dates.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const session = { id: 1 } as SessionRecord;

type Runner = Pick<SideRunner, "run">;

const fakeRunner = () => {
  const run = vi.fn().mockResolvedValue({ text: "done" });
  const side: Runner = { run };
  return { side, run };
};

const writeTranscript = async (path: string): Promise<void> => {
  const lines = [
    { type: "session", version: 3, id: "sess-1", cwd: "/w" },
    {
      type: "message",
      id: "1",
      parentId: null,
      message: { role: "user", content: "please keep answers shorter", timestamp: 1 },
    },
    {
      type: "message",
      id: "2",
      parentId: "1",
      message: { role: "assistant", content: [{ type: "text", text: "Got it." }] },
    },
  ];

  await writeFile(path, lines.map((entry) => JSON.stringify(entry)).join("\n"), "utf8");
};

let workspace: string;
let dataDir: string;
let transcriptPath: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-context-"));
  dataDir = join(workspace, ".tachikoma");
  await mkdir(dataDir, { recursive: true });
  transcriptPath = join(workspace, "session.jsonl");
  await writeTranscript(transcriptPath);
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("core context processor", () => {
  it("runs a headless update targeting the root context files", async () => {
    const { side, run } = fakeRunner();
    const processor = createCoreContextProcessor({ side, workspaceRoot: workspace, dataDir });

    expect(processor.name).toBe("core-context");
    expect(processor.phase).toBe("preFinalize");

    await processor.process({ session, transcriptPath, log: fakeLog });

    expect(run).toHaveBeenCalledTimes(1);
    const options = run.mock.calls[0]?.[0];
    expect(options.tier).toBe("processor");
    expect(options.tools).toEqual(["read", "grep", "find", "ls", "edit", "write"]);
    expect(options.system).toContain(`${workspace}/SOUL.md`);
    expect(options.system).toContain(`${workspace}/USER.md`);
    expect(options.system).toContain(`${workspace}/AGENTS.md`);
    expect(options.system).toContain("No pending signals at this time.");
    expect(options.prompt).toContain("user: please keep answers shorter");
  });

  it("injects the pending signals snapshot into the system prompt", async () => {
    const recent = localIsoDate();
    await writeFile(
      join(dataDir, PENDING_SIGNALS_FILENAME),
      `# Pending Signals\n\n- **${recent}**: User seemed to prefer shorter responses\n`,
      "utf8",
    );

    const { side, run } = fakeRunner();
    await createCoreContextProcessor({ side, workspaceRoot: workspace, dataDir }).process({
      session,
      transcriptPath,
      log: fakeLog,
    });

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain(`S1: **${recent}**: User seemed to prefer shorter responses`);
    expect(system).toContain(join(dataDir, PENDING_SIGNALS_FILENAME));
  });

  it("expires old pending signals before reading the snapshot", async () => {
    const recent = localIsoDate();
    const signalsFile = join(dataDir, PENDING_SIGNALS_FILENAME);
    await writeFile(
      signalsFile,
      `# Pending Signals\n\n- **2020-01-01**: ancient signal\n- **${recent}**: fresh signal\n`,
      "utf8",
    );

    const { side, run } = fakeRunner();
    await createCoreContextProcessor({ side, workspaceRoot: workspace, dataDir }).process({
      session,
      transcriptPath,
      log: fakeLog,
    });

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("fresh signal");
    expect(system).not.toContain("ancient signal");
    expect(await readFile(signalsFile, "utf8")).not.toContain("ancient signal");
  });

  it("skips when there is no transcript", async () => {
    const { side, run } = fakeRunner();

    await createCoreContextProcessor({ side, workspaceRoot: workspace, dataDir }).process({
      session,
      transcriptPath: null,
      log: fakeLog,
    });

    expect(run).not.toHaveBeenCalled();
  });
});

describe("cleanPendingSignals", () => {
  it("deletes the file when every entry expired", async () => {
    const signalsFile = join(dataDir, PENDING_SIGNALS_FILENAME);
    await writeFile(signalsFile, "# Pending Signals\n\n- **2020-01-01**: ancient\n", "utf8");

    await cleanPendingSignals(dataDir, fakeLog);

    await expect(readFile(signalsFile, "utf8")).rejects.toThrow();
  });

  it("no-ops when the file is missing", async () => {
    await expect(cleanPendingSignals(dataDir, fakeLog)).resolves.toBeUndefined();
  });
});

describe("parsePendingSignals", () => {
  it("parses dated entries and ignores other lines", () => {
    const parsed = parsePendingSignals(
      "# Pending Signals\n\n- **2026-06-01**: first\nnot an entry\n- **2026-06-02**: second\n",
    );

    expect(parsed).toEqual([
      { date: "2026-06-01", text: "first" },
      { date: "2026-06-02", text: "second" },
    ]);
  });
});

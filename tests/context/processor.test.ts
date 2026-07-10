import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FILE_EDIT_TOOLS } from "../../src/agent/file-tools.ts";
import type { AgentManager } from "../../src/agent/manager.ts";
import {
  cleanPendingSignals,
  createCoreContextProcessor,
  type Forker,
  PENDING_SIGNALS_FILENAME,
  parsePendingSignals,
} from "../../src/extensions/context/processor.ts";
import type { Logger } from "../../src/log.ts";
import { localIsoDate } from "../../src/util/dates.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const trunk = null;

const fakeForker = () => {
  const forkAndContinue: AgentManager["forkAndContinue"] = vi.fn().mockResolvedValue(undefined);
  const agent: Forker = { forkAndContinue };
  return { agent, forkAndContinue: forkAndContinue as ReturnType<typeof vi.fn> };
};

let workspace: string;
let dataDir: string;
const transcriptPath = "/sessions/sess-1.jsonl";

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-context-"));
  dataDir = join(workspace, ".tachikoma");
  await mkdir(dataDir, { recursive: true });
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("core context processor", () => {
  it("forks the just-ended conversation with a follow-up instruction targeting the context files", async () => {
    const { agent, forkAndContinue } = fakeForker();
    const processor = createCoreContextProcessor({ agent, workspaceRoot: workspace, dataDir });

    expect(processor.name).toBe("core-context");
    expect(processor.phase).toBe("preFinalize");

    await processor.process({ trunk, transcriptPath, log: fakeLog });

    expect(forkAndContinue).toHaveBeenCalledTimes(1);
    const [source, instruction, tier, tools] = forkAndContinue.mock.calls[0] ?? [];
    // Forks from the session's own transcript — never replays it as text.
    expect(source).toBe(transcriptPath);
    expect(tier).toBe("processor");
    // Hard-limited to file tools even though the fork reuses the live session.
    expect(tools).toEqual(FILE_EDIT_TOOLS);
    // The instruction is a follow-up user turn to the same assistant, not a persona reset.
    expect(instruction).toContain("We just finished the conversation above");
    expect(instruction).not.toContain("<conversation>");
    expect(instruction).toContain(`${workspace}/SOUL.md`);
    expect(instruction).toContain(`${workspace}/USER.md`);
    expect(instruction).toContain(`${workspace}/AGENTS.md`);
    expect(instruction).toContain("No pending signals at this time.");
    expect(instruction).toContain("SILENT background context-maintenance step");
    // "Today's date" is substituted as a configured-timezone YYYY-MM-DD (tz correctness
    // is covered by tests/util/dates.test.ts).
    expect(instruction).toMatch(/Today's date is \d{4}-\d{2}-\d{2}\./);
  });

  it("injects the pending signals snapshot into the instruction", async () => {
    const recent = localIsoDate();
    await writeFile(
      join(dataDir, PENDING_SIGNALS_FILENAME),
      `# Pending Signals\n\n- **${recent}**: User seemed to prefer shorter responses\n`,
      "utf8",
    );

    const { agent, forkAndContinue } = fakeForker();
    await createCoreContextProcessor({ agent, workspaceRoot: workspace, dataDir }).process({
      trunk,
      transcriptPath,
      log: fakeLog,
    });

    const instruction = forkAndContinue.mock.calls[0]?.[1];
    expect(instruction).toContain(`S1: **${recent}**: User seemed to prefer shorter responses`);
    expect(instruction).toContain(join(dataDir, PENDING_SIGNALS_FILENAME));
  });

  it("expires old pending signals before reading the snapshot", async () => {
    const recent = localIsoDate();
    const signalsFile = join(dataDir, PENDING_SIGNALS_FILENAME);
    await writeFile(
      signalsFile,
      `# Pending Signals\n\n- **2020-01-01**: ancient signal\n- **${recent}**: fresh signal\n`,
      "utf8",
    );

    const { agent, forkAndContinue } = fakeForker();
    await createCoreContextProcessor({ agent, workspaceRoot: workspace, dataDir }).process({
      trunk,
      transcriptPath,
      log: fakeLog,
    });

    const instruction = forkAndContinue.mock.calls[0]?.[1];
    expect(instruction).toContain("fresh signal");
    expect(instruction).not.toContain("ancient signal");
    expect(await readFile(signalsFile, "utf8")).not.toContain("ancient signal");
  });

  it("logs which context files the forked agent created, updated, or deleted", async () => {
    await writeFile(join(workspace, "USER.md"), "existing user info\n", "utf8");

    const forkAndContinue = vi.fn().mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      await writeFile(join(workspace, "AGENTS.md"), "new operational guidance\n", "utf8");
      await writeFile(join(workspace, "USER.md"), "updated user info\n", "utf8");
    });

    const info = vi.fn();
    const log = { debug: vi.fn(), info, warn: vi.fn() } as unknown as Logger;

    await createCoreContextProcessor({
      agent: { forkAndContinue },
      workspaceRoot: workspace,
      dataDir,
    }).process({ trunk, transcriptPath, log });

    expect(info).toHaveBeenCalledWith({ file: "AGENTS.md" }, "context file created");
    expect(info).toHaveBeenCalledWith({ file: "USER.md" }, "context file updated");
    expect(info).not.toHaveBeenCalledWith({ file: "SOUL.md" }, expect.anything());
  });

  it("skips when there is no transcript", async () => {
    const { agent, forkAndContinue } = fakeForker();

    await createCoreContextProcessor({ agent, workspaceRoot: workspace, dataDir }).process({
      trunk,
      transcriptPath: null,
      log: fakeLog,
    });

    expect(forkAndContinue).not.toHaveBeenCalled();
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

  it("deletes a header-only file without warning", async () => {
    const signalsFile = join(dataDir, PENDING_SIGNALS_FILENAME);
    await writeFile(signalsFile, "# Pending Signals\n\n", "utf8");
    const warn = vi.fn();
    const log = { debug: vi.fn(), info: vi.fn(), warn } as unknown as Logger;

    await cleanPendingSignals(dataDir, log);

    await expect(readFile(signalsFile, "utf8")).rejects.toThrow();
    expect(warn).not.toHaveBeenCalled();
  });

  it("warns when the file has genuine content but no parseable entries", async () => {
    const signalsFile = join(dataDir, PENDING_SIGNALS_FILENAME);
    await writeFile(signalsFile, "# Pending Signals\n\nsome malformed note\n", "utf8");
    const warn = vi.fn();
    const log = { debug: vi.fn(), info: vi.fn(), warn } as unknown as Logger;

    await cleanPendingSignals(dataDir, log);

    expect(warn).toHaveBeenCalledWith(
      { file: signalsFile },
      "pending signals file has content but no parseable entries",
    );
    expect(await readFile(signalsFile, "utf8")).toContain("some malformed note");
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

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  filterEligible,
  IMPACT_LOG_FILENAME,
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
  listPatternPages,
  readImpactLog,
  updateEntryStatus,
  writeImpactLog,
} from "../../src/extensions/skill-evolution/store.ts";
import type { Logger } from "../../src/log.ts";
import { MEMORY_INDEX_FILENAME } from "../../src/util/markdown-store.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

let dir: string;
const logPath = (): string => join(dir, IMPACT_LOG_FILENAME);

const entry = (overrides: Partial<ImpactLogEntry> = {}): ImpactLogEntry => ({
  date: "2026-08-30",
  skill: "commit",
  pattern: "commit-flag-missing.md",
  branch: "skill-evolution/commit-flag-missing",
  tip: "abc123def4567890abc123def4567890abc1234d",
  description: "Add the -s flag to the commit guidance",
  status: IMPACT_LOG_STATUSES.proposed,
  ...overrides,
});

const writeRawLog = async (content: string): Promise<void> => writeFile(logPath(), content, "utf8");

// A hand-written ledger in the shape a user might edit: link-form pattern cells plus a bare
// filename, so both parse paths are exercised on real text rather than only via round-trips.
const HAND_WRITTEN_LOG = `${[
  "# Skill Impact Log",
  "",
  "Some editorial note from the user.",
  "",
  "| Date | Skill | Pattern | Branch | Tip | Description | Status |",
  "| ---- | ----- | ------- | ------ | --- | ----------- | ------ |",
  "| 2026-08-29 | commit | [commit-flag-missing](./commit-flag-missing.md) | skill-evolution/commit-flag-missing | tip0 | describe it | proposed |",
  "| 2026-08-28 | review | review-checklist.md | skill-evolution/review-checklist | tip1 | checklist gap | accepted |",
].join("\n")}\n`;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-skill-evo-store-"));
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
  vi.clearAllMocks();
});

describe("impact-log round-trip", () => {
  it("write → read yields identical rows (pipes in cells survive)", async () => {
    const rows = [
      entry(),
      entry({
        date: "2026-08-29",
        skill: "review",
        pattern: "review-checklist.md",
        branch: "skill-evolution/review-checklist",
        tip: "fff123def4567890abc123def4567890abc1234d",
        description: "Add | document to the checklist",
        status: IMPACT_LOG_STATUSES.accepted,
      }),
    ];

    await writeImpactLog(logPath(), rows);

    await expect(readImpactLog(logPath(), fakeLog)).resolves.toEqual(rows);
    expect(fakeLog.warn).not.toHaveBeenCalled();
  });

  it("reads a missing ledger as empty (ENOENT-tolerant)", async () => {
    await expect(readImpactLog(logPath(), fakeLog)).resolves.toEqual([]);
  });

  it("parses a hand-written ledger: link-form and bare-filename pattern cells", async () => {
    await writeRawLog(HAND_WRITTEN_LOG);

    await expect(readImpactLog(logPath(), fakeLog)).resolves.toEqual([
      entry({
        date: "2026-08-29",
        pattern: "commit-flag-missing.md",
        tip: "tip0",
        description: "describe it",
      }),
      entry({
        date: "2026-08-28",
        skill: "review",
        pattern: "review-checklist.md",
        branch: "skill-evolution/review-checklist",
        tip: "tip1",
        description: "checklist gap",
        status: IMPACT_LOG_STATUSES.accepted,
      }),
    ]);
  });

  it("a status write-back keeps every row, including rows whose pattern page is not on disk", async () => {
    // No pattern page exists in this temp dir — both rows name a page that is absent.
    const settled = entry({
      tip: "tip-settled",
      status: IMPACT_LOG_STATUSES.accepted,
    });
    const open = entry({
      pattern: "review-checklist.md",
      branch: "skill-evolution/review-checklist",
      tip: "tip-open",
    });

    await writeImpactLog(logPath(), [settled, open]);

    // Reconciliation's write path: parse, rewrite one status, write the whole ledger back.
    const rows = await readImpactLog(logPath(), fakeLog);
    await writeImpactLog(
      logPath(),
      updateEntryStatus(rows, open.branch, open.tip, IMPACT_LOG_STATUSES.accepted),
    );

    const reread = await readImpactLog(logPath(), fakeLog);

    expect(reread).toHaveLength(2);
    expect(reread[0]).toEqual(settled);
    expect(reread[1]?.status).toBe(IMPACT_LOG_STATUSES.accepted);
    expect(fakeLog.warn).not.toHaveBeenCalled();
  });
});

describe("lenient parsing", () => {
  it("skips malformed rows with a warn, keeping the well-formed ones", async () => {
    await writeRawLog(
      `${[
        "| Date | Skill | Pattern | Branch | Tip | Description | Status |",
        "| ---- | ----- | ------- | ------ | --- | ----------- | ------ |",
        // Too few cells.
        "| 2026-08-29 | commit | commit-flag-missing.md | skill-evolution/a | tip | proposed |",
        // Unknown status.
        "| 2026-08-29 | commit | commit-flag-missing.md | skill-evolution/b | tip | desc | maybe |",
        // Empty required cell.
        "| 2026-08-29 | commit | commit-flag-missing.md |  | tip | desc | proposed |",
        // Well-formed.
        "| 2026-08-29 | commit | commit-flag-missing.md | skill-evolution/d | tip | desc | rejected |",
      ].join("\n")}\n`,
    );

    const rows = await readImpactLog(logPath(), fakeLog);

    expect(rows).toHaveLength(1);
    expect(rows[0]?.branch).toBe("skill-evolution/d");
    expect(rows[0]?.status).toBe(IMPACT_LOG_STATUSES.rejected);
    expect(fakeLog.warn).toHaveBeenCalledTimes(3);
  });

  it("never throws on a table with no separator (header row fails status validation)", async () => {
    await writeRawLog(
      ["| Date | Skill | Pattern | Branch | Tip | Description | Status |"].join("\n"),
    );

    await expect(readImpactLog(logPath(), fakeLog)).resolves.toEqual([]);
    expect(fakeLog.warn).toHaveBeenCalled();
  });
});

describe("row mutations", () => {
  it("updateEntryStatus rewrites only the row keyed by branch + tip", () => {
    const first = entry({ branch: "skill-evolution/a", tip: "tip-a" });
    const second = entry({ branch: "skill-evolution/b", tip: "tip-b" });

    const rows = updateEntryStatus(
      [first, second],
      "skill-evolution/b",
      "tip-b",
      IMPACT_LOG_STATUSES.accepted,
    );

    expect(rows).toHaveLength(2);
    expect(rows[0]).toBe(first);
    expect(rows[1]?.status).toBe(IMPACT_LOG_STATUSES.accepted);
  });

  it("updateEntryStatus with an unknown key returns the rows unchanged", () => {
    const rows = [entry()];

    expect(
      updateEntryStatus(rows, "skill-evolution/absent", "tip-absent", IMPACT_LOG_STATUSES.rejected),
    ).toEqual(rows);
  });
});

describe("listPatternPages", () => {
  it("inventories pattern pages, excluding the index and the ledger", async () => {
    await writeFile(join(dir, "pattern-a.md"), "a", "utf8");
    await writeFile(join(dir, "pattern-b.md"), "b", "utf8");
    await writeFile(join(dir, MEMORY_INDEX_FILENAME), "# Index", "utf8");
    await writeFile(join(dir, IMPACT_LOG_FILENAME), "# Ledger", "utf8");
    await writeFile(join(dir, "notes.txt"), "n", "utf8");

    await expect(listPatternPages(dir)).resolves.toEqual(["pattern-a.md", "pattern-b.md"]);
  });
});

describe("filterEligible", () => {
  it("drops patterns carrying an entry in any status (never re-proposed)", () => {
    for (const status of Object.values(IMPACT_LOG_STATUSES)) {
      const eligible = filterEligible(
        ["pattern-a.md", "pattern-b.md"],
        [entry({ pattern: "pattern-a.md", status })],
        fakeLog,
      );

      expect(eligible).toEqual(["pattern-b.md"]);
    }

    // Every row names a live page: nothing to warn about and nothing to aggregate.
    expect(fakeLog.warn).not.toHaveBeenCalled();
    expect(fakeLog.debug).not.toHaveBeenCalled();
  });

  it("keeps every pattern when the log is empty", () => {
    expect(filterEligible(["pattern-a.md"], [], fakeLog)).toEqual(["pattern-a.md"]);

    expect(fakeLog.debug).not.toHaveBeenCalled();
  });

  it("treats a row naming no live page as history in any status — no warn, one aggregate", () => {
    const absent = Object.values(IMPACT_LOG_STATUSES).map((status, index) =>
      entry({ pattern: `retired-${index}.md`, status }),
    );

    const eligible = filterEligible(
      ["pattern-a.md", "pattern-b.md"],
      // One row names a live page, so the two counts in the aggregate differ.
      [...absent, entry({ pattern: "pattern-a.md" })],
      fakeLog,
    );

    expect(eligible).toEqual(["pattern-b.md"]);
    expect(fakeLog.warn).not.toHaveBeenCalled();
    expect(fakeLog.debug).toHaveBeenCalledTimes(1);
    expect(fakeLog.debug).toHaveBeenCalledWith(
      { rows: 4, withoutLivePage: 3 },
      "impact-ledger rows without a live pattern page — history, nothing blocked",
    );
  });

  it("warns once for a reference that can never name a page, and blocks nothing", () => {
    const unresolvable = [
      "deploy-env-flag",
      "sub/deploy-env-flag.md",
      MEMORY_INDEX_FILENAME,
      IMPACT_LOG_FILENAME,
    ];

    for (const pattern of unresolvable) {
      vi.clearAllMocks();

      const eligible = filterEligible(
        ["pattern-a.md"],
        [entry({ pattern, branch: "skill-evolution/broken-reference" })],
        fakeLog,
      );

      expect(eligible).toEqual(["pattern-a.md"]);
      expect(fakeLog.warn).toHaveBeenCalledTimes(1);
      expect(fakeLog.warn).toHaveBeenCalledWith(
        { pattern, branch: "skill-evolution/broken-reference" },
        "impact-log row's pattern reference cannot name a pattern page — it blocks nothing",
      );
      // Still a row without a live page: it is counted, never dropped.
      expect(fakeLog.debug).toHaveBeenCalledWith(
        { rows: 1, withoutLivePage: 1 },
        expect.any(String),
      );
    }
  });
});

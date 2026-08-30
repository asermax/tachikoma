import { readFile, writeFile } from "node:fs/promises";

import type { Logger } from "../../log.ts";
import { fileExists, listMarkdown } from "../../util/markdown-store.ts";

/**
 * The skill-impact ledger: the store's one host-written file. Every row is a verified proposal
 * (branch + tip read from git state), and reconciliation rewrites statuses in place — no LLM
 * ever writes a SHA or a status (S6, R5). Parsing is lenient by contract: the file lives in the
 * user's workspace and is editable, so a malformed row is warned and skipped, never fatal.
 */

/** Filename of the ledger inside the skill-evolution store dir. */
export const IMPACT_LOG_FILENAME = "skill-impact-log.md";

/** The proposal lifecycle statuses (R1/R5). */
export const IMPACT_LOG_STATUSES = {
  proposed: "proposed",
  accepted: "accepted",
  rejected: "rejected",
} as const;

export type ImpactLogStatus = (typeof IMPACT_LOG_STATUSES)[keyof typeof IMPACT_LOG_STATUSES];

const STATUS_SET: ReadonlySet<string> = new Set<string>(Object.values(IMPACT_LOG_STATUSES));

export interface ImpactLogEntry {
  /** Calendar day (`YYYY-MM-DD`) the proposal was verified. */
  date: string;
  /** Workspace skill the proposal modifies. */
  skill: string;
  /** Pattern page the proposal came from (a `.md` filename within the store dir). */
  pattern: string;
  /** Pushed proposal branch (`skill-evolution/<skill>-<slug>`). */
  branch: string;
  /** Remote tip SHA of the pushed branch at verification time. */
  tip: string;
  /** One-line description of the proposed change. */
  description: string;
  status: ImpactLogStatus;
}

const COLUMN_COUNT = 7;

const TABLE_HEADER = "| Date | Skill | Pattern | Branch | Tip | Description | Status |";
const TABLE_SEPARATOR = "| ---- | ----- | ------- | ------ | --- | ----------- | ------ |";

// A literal pipe in a cell must be escaped so it can never split the row.
const escapeCell = (value: string): string =>
  value.replaceAll("|", "\\|").replaceAll(/\r?\n/g, " ").trim();

// The pattern column renders as a store-relative link so the ledger reads like the wiki index;
// parsing accepts both this form and a bare filename (lenient to user edits).
const patternCell = (pattern: string): string => `[${pattern.replace(/\.md$/, "")}](./${pattern})`;

const PATTERN_LINK = /^\[([^\]]*)\]\(([^)]+)\)$/;

const patternFromCell = (cellValue: string): string =>
  (PATTERN_LINK.exec(cellValue)?.[2] ?? cellValue).replace(/^\.\//, "");

// Split a table line into trimmed cells, honoring `\|` escapes.
const splitRow = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split(/(?<!\\)\|/)
    .map((cellValue) => cellValue.trim().replaceAll("\\|", "|"));

const isSeparatorRow = (cells: string[]): boolean =>
  cells.length > 0 && cells.every((cellValue) => /^:?-+:?$/.test(cellValue));

/**
 * Render the ledger deterministically from rows: title, fixed table header, one line per row in
 * insertion order. The bootstrap seed and every write-back share this shape; the one
 * cell-level normalization on a write-back is the Pattern column — a user-edited bare filename
 * comes back as a store-relative link (parsing accepts both forms).
 */
export const formatImpactLog = (rows: readonly ImpactLogEntry[]): string =>
  `${[
    "# Skill Impact Log",
    "",
    "One row per skill-evolution proposal; statuses are reconciled from remote state on every run.",
    "",
    TABLE_HEADER,
    TABLE_SEPARATOR,
    ...rows.map(
      (row) =>
        `| ${escapeCell(row.date)} | ${escapeCell(row.skill)} | ${patternCell(row.pattern)} | ${escapeCell(row.branch)} | ${escapeCell(row.tip)} | ${escapeCell(row.description)} | ${escapeCell(row.status)} |`,
    ),
  ].join("\n")}\n`;

/**
 * Parse the ledger. Rows that do not match the table shape (wrong cell count, empty required
 * cell, unknown status) are warned and skipped; a missing file reads as empty. Never throws.
 */
export const readImpactLog = async (path: string, log: Logger): Promise<ImpactLogEntry[]> => {
  if (!(await fileExists(path))) return [];

  const tableLines = (await readFile(path, "utf8"))
    .split(/\r?\n/)
    .filter((line) => line.trim().startsWith("|"));

  // Data rows live after the header's separator line. With no separator at all (hand-mangled
  // file), every table line is treated as data — the header row then fails validation and is
  // skipped below, so the file still parses leniently.
  const separatorIndex = tableLines.findIndex((line) => isSeparatorRow(splitRow(line)));
  const dataLines = separatorIndex === -1 ? tableLines : tableLines.slice(separatorIndex + 1);

  const rows: ImpactLogEntry[] = [];

  for (const line of dataLines) {
    const cells = splitRow(line);

    if (cells.length !== COLUMN_COUNT) {
      log.warn({ path, line }, "impact-log row does not match the table shape — skipped");
      continue;
    }

    const [date, skill, patternColumn, branch, tip, description, status] = cells as [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
    ];

    if (
      !STATUS_SET.has(status) ||
      date === "" ||
      skill === "" ||
      patternColumn === "" ||
      branch === "" ||
      tip === "" ||
      description === ""
    ) {
      log.warn({ path, line }, "impact-log row is malformed — skipped");
      continue;
    }

    rows.push({
      date,
      skill,
      pattern: patternFromCell(patternColumn),
      branch,
      tip,
      description,
      status: status as ImpactLogStatus,
    });
  }

  return rows;
};

/**
 * Rewrite the status of the row keyed by `branch` + `tip` — the pair identifies exactly one
 * proposal, so reconciliation updates one row and never touches its neighbors. A key matching
 * no row returns the rows unchanged.
 */
export const updateEntryStatus = (
  rows: readonly ImpactLogEntry[],
  branch: string,
  tip: string,
  status: ImpactLogStatus,
): ImpactLogEntry[] =>
  rows.map((row) => (row.branch === branch && row.tip === tip ? { ...row, status } : row));

export const writeImpactLog = async (
  path: string,
  rows: readonly ImpactLogEntry[],
): Promise<void> => writeFile(path, formatImpactLog(rows), "utf8");

/**
 * Pattern pages in the store dir: everything markdown except the `MEMORY.md` index (excluded by
 * `listMarkdown`) and the impact ledger (this store's second non-page file).
 */
export const listPatternPages = async (dir: string): Promise<string[]> =>
  (await listMarkdown(dir)).filter((name) => name !== IMPACT_LOG_FILENAME);

/**
 * The never-re-proposed rule, enforced input-side (R1/R8): a pattern page carrying ANY ledger
 * entry — `proposed`, `accepted`, or `rejected` — is dropped from the proposal candidates. A
 * row whose linked pattern page no longer exists on disk (user-edited store) is warned and
 * skipped: it can only block a pattern that isn't a candidate anyway. Never fatal.
 */
export const filterEligible = (
  patterns: readonly string[],
  logRows: readonly ImpactLogEntry[],
  log: Logger,
): string[] => {
  const pages = new Set(patterns);
  const blocked = new Set<string>();

  for (const row of logRows) {
    if (!pages.has(row.pattern)) {
      log.warn(
        { pattern: row.pattern, branch: row.branch },
        "impact-log row references a missing pattern page — skipped",
      );
      continue;
    }

    blocked.add(row.pattern);
  }

  return patterns.filter((pattern) => !blocked.has(pattern));
};

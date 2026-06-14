import convert from "telegramify-markdown";

/** A fenced-code-block delimiter: ``` or ~~~ (with optional language hint). */
const FENCE_PATTERN = /^\s*(```|~~~)/;

/**
 * GFM tables have no MarkdownV2 representation: `telegramify-markdown`'s escape
 * mode double-escapes table cell content (a cell `7-8` becomes `7\\-8`, leaving
 * an unescaped `-` that Telegram rejects), and Telegram can't render real tables
 * anyway. `flattenTables` rewrites each GFM table into a flat bullet list *before*
 * conversion, so no table structure reaches the converter and the surrounding
 * inline formatting (bold, italic, code) survives.
 */
export const flattenTables = (text: string): string => {
  const lines = text.split("\n");
  const result: string[] = [];
  let inFence = false;

  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";

    if (FENCE_PATTERN.test(line.trim())) {
      inFence = !inFence;
      result.push(line);
      i += 1;
      continue;
    }

    const next = lines[i + 1];
    // A table is a pipe row whose very next line is a GFM separator. Requiring
    // the separator is what stops ordinary pipe-bearing prose being misread.
    if (!inFence && next !== undefined && isTableRow(line) && isTableSeparator(next)) {
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length) {
        const rowLine = lines[j];
        if (rowLine === undefined || !isTableRow(rowLine)) break;
        rows.push(parseCells(rowLine));
        j += 1;
      }
      // Drop the header row (columns are usually self-evident from the data);
      // if the table has no data rows, keep the header text so nothing is lost.
      const toRender = rows.length > 0 ? rows : [parseCells(line)];
      result.push(renderRows(toRender));
      i = j;
      continue;
    }

    result.push(line);
    i += 1;
  }

  return result.join("\n");
};

/** A GFM table separator row: pipe-joined cells of dashes (with optional alignment colons). */
const isTableSeparator = (line: string): boolean => {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  const inner = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  return inner.split("|").every((cell) => /^:?-+:?$/.test(cell.trim()));
};

/** A non-empty line containing a pipe that is not itself a separator. */
const isTableRow = (line: string): boolean => {
  if (isTableSeparator(line)) return false;
  const trimmed = line.trim();
  return trimmed.length > 0 && trimmed.includes("|");
};

/** Split a table row into trimmed cells, ignoring a single border pipe at each end. */
const parseCells = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());

/**
 * Render table data rows as bullets: the first cell is bolded as a label and the
 * remaining cells are joined by a middle dot. Inline markdown inside cells is
 * preserved verbatim — the later MarkdownV2 conversion escapes it correctly.
 */
const renderRows = (rows: string[][]): string =>
  rows
    .map((cells) => {
      if (cells.length <= 1) return `- ${cells[0] ?? ""}`;
      const first = (cells[0] ?? "").trim();
      const rest = cells.slice(1).join(" · ");
      return first.length > 0 ? `- **${first}**: ${rest}` : `- ${rest}`;
    })
    .join("\n");

/**
 * Convert the agent's GitHub-flavored markdown into the Telegram MarkdownV2
 * dialect. Telegram's own parsers don't understand GFM and reject whole
 * messages on any unescaped punctuation (`.`, `-`, `!`, `(`, …); telegramify
 * rewrites the constructs Telegram supports (bold, italic, code, links, lists)
 * and escapes everything else, mirroring the legacy Python channel. GFM tables
 * are flattened to a bullet list first (see `flattenTables`) since the converter
 * mishandles their cells and Telegram has no table rendering. "escape" renders
 * unsupported HTML as visible escaped text rather than silently dropping it.
 */
export const toTelegramMarkdown = (text: string): string => convert(flattenTables(text), "escape");

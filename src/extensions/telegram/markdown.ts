/** A fenced-code-block delimiter: ``` or ~~~ (with optional language hint). */
const FENCE_PATTERN = /^\s*(```|~~~)/;

/**
 * GFM tables have no Telegram representation: there is no table `MessageEntity`
 * and Telegram can't render real tables, so a table reaching the entity converter
 * would either lose its structure or produce broken offsets. `flattenTables`
 * rewrites each GFM table into a flat bullet list *before* conversion, so no table
 * structure reaches the converter and the surrounding inline formatting (bold,
 * italic, code) survives as entities. The header row is preserved as a fully-bold
 * first bullet so the reader can tell what each column represents; data rows keep
 * their bold-label bullet form.
 */
export const flattenTables = (text: string): string => {
  // No pipe means no possible GFM table; skip the line scan entirely. This is the
  // common case (most messages have no table) and flattenTables runs on every send.
  if (!text.includes("|")) return text;

  const lines = text.split("\n");
  const result: string[] = [];
  let inFence = false;

  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";

    if (FENCE_PATTERN.test(line)) {
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
      // Preserve the header as a fully-bold first bullet so the reader can tell
      // what each column represents; data rows follow in their existing format.
      const header = parseCells(line);
      result.push([renderHeader(header), ...rows.map(renderRow)].join("\n"));
      i = j;
      continue;
    }

    result.push(line);
    i += 1;
  }

  return result.join("\n");
};

/** Strip a single leading/trailing border pipe from a table row. */
const stripBorders = (line: string): string => line.replace(/^\|/, "").replace(/\|$/, "");

/** A GFM table separator row: pipe-joined cells of dashes (with optional alignment colons). */
const isTableSeparator = (line: string): boolean =>
  stripBorders(line.trim())
    .split("|")
    .every((cell) => /^:?-+:?$/.test(cell.trim()));

/** A non-empty line containing a pipe that is not itself a separator. */
const isTableRow = (line: string): boolean => {
  if (isTableSeparator(line)) return false;
  const trimmed = line.trim();
  return trimmed.length > 0 && trimmed.includes("|");
};

/** Split a table row into trimmed cells, ignoring a single border pipe at each end. */
const parseCells = (line: string): string[] =>
  stripBorders(line.trim())
    .split("|")
    .map((cell) => cell.trim());

/**
 * Render one table data row as a bullet: the first cell is bolded as a label and
 * the remaining cells are joined by a middle dot. Inline markdown inside cells is
 * preserved verbatim — the later entity conversion turns it into spans.
 */
const renderRow = (cells: string[]): string => {
  if (cells.length <= 1) return `- ${cells[0] ?? ""}`;
  const first = cells[0] ?? "";
  const rest = cells.slice(1).join(" · ");
  return first.length > 0 ? `- **${first}**: ${rest}` : `- ${rest}`;
};

/**
 * Render the header row as a fully-bold first bullet so it reads as the column
 * titles rather than a data row: every non-empty cell is bolded and the cells are
 * joined by a middle dot (data rows bold only the first cell and follow it with a
 * colon). An empty header cell is left plain so no broken `****` markup is emitted.
 */
const renderHeader = (cells: string[]): string => {
  if (cells.length <= 1) {
    const only = cells[0] ?? "";
    return only.length > 0 ? `- **${only}**` : `- ${only}`;
  }
  return `- ${cells.map((cell) => (cell.length > 0 ? `**${cell}**` : cell)).join(" · ")}`;
};

const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const ITALIC = "\x1b[3m";
const UNDERLINE = "\x1b[4m";
const CYAN = "\x1b[36m";
const YELLOW = "\x1b[33m";

const HEADING = /^(#{1,6})\s+(.*)$/;
const LIST_ITEM = /^(\s*)([-*+]|\d+\.)\s+(.*)$/;
const FENCE = /^\s*```(.*)$/;

/**
 * Render a finalized markdown string as ANSI-styled terminal text.
 *
 * This renders whole blocks rather than per-chunk: inline spans (bold, code)
 * can straddle stream-chunk boundaries, so the REPL buffers an exchange's text
 * and renders the accumulated string here once it is complete. The renderer is
 * intentionally line-oriented and lossy on exotic markdown — it covers the
 * constructs the agent actually emits (headings, lists, fences, inline spans).
 */
export const renderMarkdown = (source: string): string => {
  const lines = source.split("\n");
  const out: string[] = [];

  let inFence = false;

  for (const line of lines) {
    const fence = FENCE.exec(line);

    if (fence != null) {
      inFence = !inFence;
      continue;
    }

    if (inFence) {
      out.push(`${DIM}${line}${RESET}`);
      continue;
    }

    const heading = HEADING.exec(line);

    if (heading != null) {
      out.push(`${BOLD}${UNDERLINE}${renderInline(heading[2] ?? "")}${RESET}`);
      continue;
    }

    const item = LIST_ITEM.exec(line);

    if (item != null) {
      out.push(`${item[1] ?? ""}${CYAN}•${RESET} ${renderInline(item[3] ?? "")}`);
      continue;
    }

    out.push(renderInline(line));
  }

  return out.join("\n");
};

const INLINE_CODE = /`([^`]+)`/g;
const BOLD_SPAN = /\*\*([^*]+)\*\*|__([^_]+)__/g;
const ITALIC_SPAN = /(?<![*_])\*([^*]+)\*(?!\*)|(?<![*_])_([^_]+)_(?!_)/g;

const renderInline = (text: string): string => {
  let result = text.replace(INLINE_CODE, (_match, code) => `${YELLOW}${code}${RESET}`);

  result = result.replace(
    BOLD_SPAN,
    (_match, stars, unders) => `${BOLD}${stars ?? unders}${RESET}`,
  );

  result = result.replace(
    ITALIC_SPAN,
    (_match, stars, unders) => `${ITALIC}${stars ?? unders}${RESET}`,
  );

  return result;
};

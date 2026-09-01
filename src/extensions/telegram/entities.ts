import type { MessageEntity } from "grammy/types";
import MarkdownIt from "markdown-it";
import { isLowSurrogate, TELEGRAM_MAX_MESSAGE_LENGTH } from "./chunking.ts";
import { flattenTables } from "./markdown.ts";

/** A converted message ready for `sendMessage`/`editMessageText` with `parse_mode` omitted. */
export type TelegramPayload = { text: string; entities: MessageEntity[] };

/**
 * The markdown-it token fields the walker reads. A structural subset of the
 * library's `Token` so this module doesn't depend on the exact type export shape.
 */
interface MdToken {
  type: string;
  tag: string;
  nesting: number;
  content: string;
  info: string;
  attrs: [string, string][] | null;
  children: MdToken[] | null;
}

/**
 * Shared parser. The default preset enables GFM strikethrough; `html`/`linkify`/
 * `typographer` are off so stray HTML renders literal, only explicit links become
 * `text_link` entities (no false positives like `1.2.3`), and soft breaks stay
 * CommonMark. GFM tables never reach here — `flattenTables` rewrites them first.
 */
const md = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
  breaks: false,
});

const attr = (token: MdToken, name: string): string | undefined =>
  token.attrs?.find(([key]) => key === name)?.[1];

/**
 * Telegram accepts only "HTTP or tg:// URL" targets on `text_link` entities and
 * rejects anything else with a 400 "entity URL ... is invalid" — which would fail the
 * whole message, not just the link. WHATWG parsing rejects scheme-less relative targets
 * outright and normalizes scheme case, so the protocol comparison is the whole check.
 * (Parseable-but-nonstandard targets like `http:foo` still pass; the send path's
 * plain-text fallback covers any rejection Telegram makes beyond this.)
 */
const isSupportedLinkUrl = (href: string): boolean => {
  try {
    const { protocol } = new URL(href);
    return protocol === "http:" || protocol === "https:" || protocol === "tg:";
  } catch {
    return false;
  }
};

/**
 * What to append after a link's already-written label when Telegram forbids the
 * target — the reference stays visible: bare target when there is no label text
 * (image links write nothing), parenthesized target otherwise, and nothing when
 * the label already is the target.
 */
const downgradedLinkSuffix = (label: string, href: string): string =>
  label.length === 0 ? href : label === href ? "" : ` (${href})`;

/**
 * Accumulates literal display text and `MessageEntity` spans while walking the
 * token stream. Text outside entities needs no escaping — Telegram treats it as
 * literal when sent with `parse_mode` omitted, so `.`, `-`, `!`, `(` etc. are
 * never a problem. Entity offsets are UTF-16 code units (JS string indices),
 * which is exactly what Telegram measures.
 */
class Renderer {
  text = "";
  readonly entities: MessageEntity[] = [];
  private readonly open: Array<{
    start: number;
    type: MessageEntity["type"];
    extra?: Record<string, unknown>;
  }> = [];
  private pendingSep = 0;
  private suppressGap = false;

  private flushSep(): void {
    if (this.pendingSep > 0) {
      this.text += "\n".repeat(this.pendingSep);
      this.pendingSep = 0;
    }
  }

  /** Request `n` newlines before the next written text (no-op at the very start). */
  gap(n = 2): void {
    if (this.suppressGap) {
      this.suppressGap = false;
      return;
    }
    if (this.text.length > 0) this.pendingSep = Math.max(this.pendingSep, n);
  }

  /** Make the next `gap()` a no-op — so a container's first inner block continues right after the opening marker (a list bullet, a blockquote start). */
  suppressNextGap(): void {
    this.suppressGap = true;
  }

  /** The current write position, flushing any pending separator first. */
  position(): number {
    this.flushSep();
    return this.text.length;
  }

  /** Append literal text, flushing any pending separator first; returns its start offset. */
  write(s: string): number {
    if (s.length === 0) return this.text.length;
    const start = this.position();
    this.text += s;
    return start;
  }

  /** Open a formatting span at the current position. */
  beginSpan(type: MessageEntity["type"], extra?: Record<string, unknown>): void {
    this.open.push({ start: this.position(), type, extra });
  }

  /** Close the most recent span, recording an entity over the text written since it opened. */
  endSpan(): void {
    const span = this.open.pop();
    if (!span) return;
    this.entities.push({
      type: span.type,
      offset: span.start,
      length: this.text.length - span.start,
      ...(span.extra ?? {}),
    } as MessageEntity);
  }

  /** Record an entity over text just written via `write` (for self-closing tokens: code, pre). */
  entityAt(start: number, type: MessageEntity["type"], extra?: Record<string, unknown>): void {
    this.entities.push({
      type,
      offset: start,
      length: this.text.length - start,
      ...(extra ?? {}),
    } as MessageEntity);
  }
}

/** Walk inline tokens (the `children` of an `inline` block), emitting text + spans. */
const renderInline = (tokens: MdToken[] | null, r: Renderer): void => {
  if (!tokens) return;
  // The one open link — links cannot nest in CommonMark. `"span"` when a `text_link`
  // entity was opened; `{ start, href }` when the target is unsupported, so no span
  // was opened and `link_close` splices the target in as plain text instead; null
  // when this link opened nothing (no href).
  let link: "span" | { start: number; href: string } | null = null;
  for (const t of tokens) {
    switch (t.type) {
      case "text":
        r.write(t.content);
        break;
      case "softbreak":
      case "hardbreak":
        r.write("\n");
        break;
      case "strong_open":
        r.beginSpan("bold");
        break;
      case "strong_close":
        r.endSpan();
        break;
      case "em_open":
        r.beginSpan("italic");
        break;
      case "em_close":
        r.endSpan();
        break;
      case "s_open":
        r.beginSpan("strikethrough");
        break;
      case "s_close":
        r.endSpan();
        break;
      case "code_inline": {
        const start = r.write(t.content);
        r.entityAt(start, "code");
        break;
      }
      case "link_open": {
        const href = attr(t, "href");
        // Supported target → `text_link` span; unsupported → track the label start
        // for a plain-text splice at close; missing/empty href (markdown-it emits
        // href="" for `[x]()`) → plain label, no span to pop.
        if (href && isSupportedLinkUrl(href)) {
          r.beginSpan("text_link", { url: href });
          link = "span";
        } else if (href) {
          link = { start: r.position(), href };
        }
        break;
      }
      case "link_close": {
        // Only finish what this link opened — a stray close must not eat an
        // unrelated enclosing span (e.g. the bold around `**[x]()**`).
        if (link === "span") r.endSpan();
        else if (link) r.write(downgradedLinkSuffix(r.text.slice(link.start), link.href));
        link = null;
        break;
      }
      case "image": {
        const alt = attr(t, "alt");
        if (alt) r.write(alt);
        break;
      }
      default:
        if (t.content) r.write(t.content);
    }
  }
};

/**
 * Process sibling block tokens from index `i` until a `stop` close token
 * (exclusive) or the end; return the index of `stop` (or `tokens.length`).
 */
const renderBlocks = (tokens: MdToken[], i: number, r: Renderer, stop: string | null): number => {
  while (i < tokens.length) {
    const t = tokens[i];
    if (!t) break;
    if (stop !== null && t.type === stop) return i;
    i = renderBlock(tokens, i, r);
  }
  return i;
};

/** Process one block token (and any nested content), returning the next index. */
const renderBlock = (tokens: MdToken[], i: number, r: Renderer): number => {
  const t = tokens[i];
  if (!t) return i + 1;
  switch (t.type) {
    case "paragraph_open":
      r.gap();
      renderInline(tokens[i + 1]?.children ?? null, r);
      return i + 3;
    case "heading_open":
      r.gap();
      r.beginSpan("bold"); // Telegram has no heading entity; bold matches legacy `*Header*`.
      renderInline(tokens[i + 1]?.children ?? null, r);
      r.endSpan();
      return i + 3;
    case "bullet_list_open":
      return renderList(tokens, i, r, false);
    case "ordered_list_open":
      return renderList(tokens, i, r, true);
    case "fence":
    case "code_block": {
      r.gap();
      // markdown-it appends a trailing newline to code content; drop one so the
      // entity covers exactly the visible lines.
      const content = t.content.endsWith("\n") ? t.content.slice(0, -1) : t.content;
      const start = r.write(content);
      const language = t.type === "fence" ? t.info.trim() : "";
      r.entityAt(start, "pre", language ? { language } : undefined);
      return i + 1;
    }
    case "hr":
      r.gap();
      r.write("———");
      return i + 1;
    case "html_block":
      r.gap();
      r.write(t.content);
      return i + 1;
    case "blockquote_open": {
      r.gap();
      r.beginSpan("blockquote");
      r.suppressNextGap(); // keep the blockquote's first line flush to the span start
      const next = renderBlocks(tokens, i + 1, r, "blockquote_close");
      r.endSpan();
      return next + 1;
    }
    default:
      // Unknown block: drop nothing — append any literal content and advance.
      if (t.content) {
        r.gap();
        r.write(t.content);
      }
      return i + 1;
  }
};

/** Render a list, prefixing each item with a bullet/number and recursing into item content. */
const renderList = (tokens: MdToken[], openIdx: number, r: Renderer, ordered: boolean): number => {
  const close = ordered ? "ordered_list_close" : "bullet_list_close";
  const openToken = tokens[openIdx];
  const startAttr = ordered && openToken ? attr(openToken, "start") : undefined;
  let index = startAttr ? Number.parseInt(startAttr, 10) || 1 : 1;
  let i = openIdx + 1;

  r.gap(); // block-break before the list (no-op at the very start)

  while (i < tokens.length) {
    const t = tokens[i];
    if (!t || t.type === close) break;
    if (t.type === "list_item_open") {
      r.gap(1); // one newline between items (a block-break before the first item)
      r.write(ordered ? `${index}. ` : "• ");
      r.suppressNextGap(); // item's first block continues on the bullet line
      i = renderBlocks(tokens, i + 1, r, "list_item_close");
      index += 1;
    }
    i += 1; // advance past `list_item_close` (or any unexpected token)
  }

  return i + 1; // past the list close
};

/**
 * Convert the agent's GitHub-flavored markdown into a Telegram entity payload:
 * literal display text plus `MessageEntity` spans for bold, italic, strikethrough,
 * code, pre(+language), text_link, blockquote, and headings (as bold). GFM tables
 * are flattened to bullets first. Constructs with no markdown mapping (spoiler,
 * underline, text_mention, custom_emoji) render as plain literal text, as do links
 * whose target Telegram forbids on `text_link` (anything but http/https/tg) — the
 * label keeps the target visible in parentheses.
 */
export const toTelegramEntities = (text: string): TelegramPayload => {
  const r = new Renderer();
  const tokens = md.parse(flattenTables(text), {}) as unknown as MdToken[];
  renderBlocks(tokens, 0, r, null);
  const entities = [...r.entities].sort((a, b) => a.offset - b.offset || a.length - b.length);
  return { text: r.text, entities };
};

/** True if split position `p` falls strictly inside an entity (would cut it). */
const isInsideEntity = (p: number, entities: MessageEntity[]): boolean =>
  entities.some((e) => e.offset < p && p < e.offset + e.length);

/**
 * The largest position `p` with `cursor < p <= maxEnd` that sits right after an
 * occurrence of `sep` and is entity-safe, or -1 if none. Preferable to a hard
 * split because it lands on a natural line/paragraph boundary.
 */
const largestSeparator = (
  text: string,
  entities: MessageEntity[],
  cursor: number,
  maxEnd: number,
  sep: string,
): number => {
  let best = -1;
  let idx = text.indexOf(sep, cursor);
  while (idx !== -1) {
    const candidate = idx + sep.length;
    if (candidate > maxEnd) break;
    if (candidate > cursor && !isInsideEntity(candidate, entities)) best = candidate;
    idx = text.indexOf(sep, idx + 1);
  }
  return best;
};

/**
 * Pick an entity-safe split position in `(cursor, maxEnd]`, preferring paragraph
 * (`\n\n`) > line (`\n`) > a surrogate-safe hard position. Because oversize
 * entities are dropped before splitting and boundaries never land inside an
 * entity, a progressing safe position always exists.
 */
const pickSplit = (
  text: string,
  entities: MessageEntity[],
  cursor: number,
  maxEnd: number,
): number => {
  const paragraph = largestSeparator(text, entities, cursor, maxEnd, "\n\n");
  if (paragraph > cursor) return paragraph;
  const line = largestSeparator(text, entities, cursor, maxEnd, "\n");
  if (line > cursor) return line;
  for (let q = maxEnd; q > cursor; q -= 1) {
    if (isInsideEntity(q, entities)) continue;
    // Don't split a surrogate pair: a low surrogate as the next chunk's first
    // code unit would orphan its high half.
    if (q < text.length && isLowSurrogate(text.charCodeAt(q))) continue;
    return q;
  }
  return maxEnd; // unreachable given the invariant; guarded for safety
};

/** Slice `[start, end)` and rebase the entities fully inside it. */
const rebase = (
  text: string,
  entities: MessageEntity[],
  start: number,
  end: number,
): TelegramPayload => ({
  text: text.slice(start, end),
  entities: entities
    .filter((e) => e.offset >= start && e.offset + e.length <= end)
    .map((e) => ({ ...e, offset: e.offset - start })),
});

/**
 * Split a converted payload into chunks of at most `limit` UTF-16 code units where
 * **no entity spans a boundary** — a formatting span is always wholly inside one
 * message, so a bold range or fenced block that crosses the length limit moves
 * wholesale into the next chunk instead of being cut in half. An entity larger
 * than `limit` (so it cannot fit any single message) is dropped up front; its text
 * still renders across chunks as plain, never as a broken half-entity. Each
 * chunk's entity offsets are relative to that chunk's own text.
 */
export const splitMessageWithEntities = (
  text: string,
  entities: MessageEntity[],
  limit: number = TELEGRAM_MAX_MESSAGE_LENGTH,
): TelegramPayload[] => {
  const usable = entities.filter((e) => e.length <= limit);
  const raw: TelegramPayload[] = [];
  let cursor = 0;

  while (cursor < text.length) {
    const maxEnd = Math.min(cursor + limit, text.length);
    const end = maxEnd < text.length ? pickSplit(text, usable, cursor, maxEnd) : maxEnd;
    raw.push(rebase(text, usable, cursor, end));
    cursor = end;
  }

  // Trim trailing newlines from each chunk (the paragraph separator that landed at
  // a chunk end) so a message never ends on blank lines. Safe because no emitted
  // entity covers trailing separator whitespace. Drop any chunk left empty.
  return raw
    .map((chunk) => ({ ...chunk, text: chunk.text.replace(/\n+$/, "") }))
    .filter((chunk) => chunk.text.length > 0);
};

/**
 * Wrap a converted payload in one collapsed `expandable_blockquote` span, flattening
 * any inner `blockquote`/`expandable_blockquote` entities to plain text. Telegram
 * forbids these from nesting each other (or themselves), and the wrapped content is
 * agent markdown that may itself contain `>` quotes — a surviving inner `blockquote`
 * would split the message into several blockquotes (or be rejected outright as "can't
 * parse entities"). The text is unchanged; only the conflicting blockquote-family
 * spans are dropped, so inner formatting (bold/italic/code/links) keeps its offsets and
 * still nests validly. The outer entity is prepended so it precedes the inner spans at
 * offset 0, matching Telegram's outer-before-inner ordering at a shared offset. See S4
 * / SPIKE-DLT-064 for the emission-path decision.
 */
export const wrapExpandable = (payload: TelegramPayload): TelegramPayload => ({
  text: payload.text,
  entities: [
    { type: "expandable_blockquote", offset: 0, length: payload.text.length },
    ...payload.entities.filter(
      (e) => e.type !== "blockquote" && e.type !== "expandable_blockquote",
    ),
  ],
});

/**
 * Join two payloads with a separator, rebasing the second's entity offsets — the
 * structured-payload composition the collapse path builds its `header ⊕ block ⊕ tail`
 * message from. `a`'s entities are already offset from the start, so they are kept
 * as-is; each of `b`'s entities is shifted by `a.text.length + sep.length` (UTF-16
 * code units, matching `rebase`'s offset math).
 *
 * Empty-operand rule: an empty operand is returned **unchanged with no separator**
 * — so the all-intensive case (no tail) drops the trailing separator cleanly and
 * yields just the left payload, rather than a payload ending on a blank line.
 */
export const concatPayloads = (
  a: TelegramPayload,
  b: TelegramPayload,
  sep = "\n\n",
): TelegramPayload => {
  if (b.text.length === 0) return a;
  if (a.text.length === 0) return b;
  const delta = a.text.length + sep.length;
  return {
    text: a.text + sep + b.text,
    entities: [...a.entities, ...b.entities.map((e) => ({ ...e, offset: e.offset + delta }))],
  };
};

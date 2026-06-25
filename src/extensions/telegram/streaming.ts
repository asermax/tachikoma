import type { DecisionHeader } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";
import { splitMessage, TELEGRAM_MAX_MESSAGE_LENGTH } from "./chunking.ts";
import {
  concatPayloads,
  splitMessageWithEntities,
  type TelegramPayload,
  toTelegramEntities,
  wrapExpandable,
} from "./entities.ts";
import {
  convertAndSplit,
  editWithFallback,
  type SendApi,
  sendChunked,
  sendWithFallback,
} from "./sending.ts";
import { formatToolActivity, summarizeToolActivities, type ToolActivity } from "./tool-labels.ts";

export const EDIT_THROTTLE_MS = 1500;

export type StreamApi = Pick<SendApi, "sendMessage" | "editMessageText" | "deleteMessage">;

/**
 * Progressive renderer for one agent exchange. Text accumulates in a buffer and
 * is held while it streams — revealed in one go when the next tool/status line
 * settles the segment (the model moved on, so the preceding text is complete) or
 * when `finalize()` flushes the remainder, so a segment is never cut mid-stream.
 * A running tool shows as a live italic line below the text; when text resumes
 * the tools that ran fold into a persistent `🔧 …` marker baked into the buffer,
 * which both records the activity and supplies the blank line separating one text
 * segment from the next. When the buffer outgrows Telegram's edit limit the full
 * chunks are finalized in place and the tail keeps streaming. Edit failures
 * degrade to the plain final-send behavior.
 */
export class StreamRenderer {
  private readonly api: StreamApi;
  private readonly chatId: number;
  private readonly log: Logger;

  private buffer = "";
  private transient: string | null = null;
  private pendingTools: ToolActivity[] = [];
  private messageId: number | null = null;
  private lastRendered = "";
  private lastEditAt = 0;
  private broken = false;
  private readonly silent: boolean;
  /** Whether intensive-work collapse is enabled (DLT-064); false ⇒ render exactly as today. */
  private readonly collapseIntensiveWork: boolean;
  /** Tool→text boundaries a message must exceed to activate collapse (DLT-064); a trigger, not a quota. */
  private readonly intensiveWorkThreshold: number;
  /**
   * Tool→text boundaries accumulated in the current uncommitted message (DLT-064). A block of one or
   * more tool calls followed by any text counts as one boundary; the count drives `collapseActive()`.
   * Reset at each message boundary (overflow commit) so the streaming tail is evaluated independently.
   */
  private boundaryCount = 0;
  /**
   * Index in `buffer` where the last content-type unit's preface begins — the start of the last text
   * segment, right after the baked summary of the prior tool group (DLT-064). The collapse
   * intermediate/tail split keys off this, not a paragraph break, so the last unit (preface text plus
   * a live or trailing tool) stays expanded as a whole and only earlier units fold. Advanced only when
   * new text resumes after a tool group (a tool-group → new-text transition, in `appendText`); reset to
   * 0 atomically with the `buffer` slice in `commitOverflow`. Always either 0 or at the start of a
   * text segment.
   */
  private tailStart = 0;
  /**
   * A turn-scoped decision header (R8) anchored above the streamed text. Set before streaming begins
   * and recomposed on every edit so the streamed body never overwrites it (KD9). Dropped (set to null)
   * once the body grows past the edit limit (best-effort) or when the renderer is finalized — never
   * carried to a later exchange.
   */
  private header: DecisionHeader | null = null;

  /**
   * @param seedMessageId An existing message id to edit in place instead of
   * sending a fresh one — used to reclaim the preparation lead-in so the streamed
   * response replaces it (or deletes it, via finalize, when the exchange has no text).
   * @param silent Send every message silently (`disable_notification`). Used while
   * push notifications are on so the streamed work-in-progress and any overflow
   * chunks never fire partial pushes — the single push is forced on completion by
   * copying the finalized message (see `forceNotification`), never by these sends.
   * @param collapseIntensiveWork Fold intensive-work sections into a collapsed
   * expandable blockquote once the boundary count exceeds the threshold (DLT-064).
   * Default on; the threshold default keeps every below-threshold turn byte-identical.
   * @param intensiveWorkThreshold Tool→text boundaries a single message must exceed
   * for collapse to activate — a trigger (count > threshold), not a quota.
   */
  constructor(
    api: StreamApi,
    chatId: number,
    log: Logger,
    seedMessageId: number | null = null,
    silent = false,
    collapseIntensiveWork = true,
    intensiveWorkThreshold = 4,
  ) {
    this.api = api;
    this.chatId = chatId;
    this.log = log;
    this.messageId = seedMessageId;
    this.silent = silent;
    this.collapseIntensiveWork = collapseIntensiveWork;
    this.intensiveWorkThreshold = intensiveWorkThreshold;
  }

  /**
   * Whether intensive-work collapse is active for the current message (DLT-064). The
   * threshold is a trigger, not a quota: collapse activates only once the boundary count
   * *strictly exceeds* it, and never when the feature is disabled — so below-threshold
   * and disabled turns render exactly as today. This is the counter's sole output; the
   * detection suite and the compose/finalize paths (Batch 3) both read it here.
   */
  collapseActive(): boolean {
    return this.collapseIntensiveWork && this.boundaryCount > this.intensiveWorkThreshold;
  }

  /**
   * Anchor a turn-scoped decision header (R8) above the streamed text. Set before streaming begins;
   * `compose()` recomposes it on every edit so the body never overwrites it. Best-effort: it is dropped
   * (and logged) if the body grows past the edit limit or a render fails.
   */
  setHeader(header: DecisionHeader): void {
    this.header = header;
  }

  async appendText(text: string): Promise<void> {
    // A boundary forms when text resumes after one or more pending tools (DLT-064). Capture before
    // bakePendingTools clears them, then count one increment for the whole tool block regardless of how
    // many tools it held — a block of N tools followed by any text is a single boundary, not N.
    const hadPending = this.pendingTools.length > 0;
    if (hadPending) this.boundaryCount += 1;
    this.bakePendingTools();
    // The baked marker belongs to the prior unit; the incoming text starts a new content-type unit, so
    // the collapse split (the start of the last text segment) advances to here — after the bake. More
    // text in the same segment leaves it untouched, keeping the whole segment expanded as one unit.
    if (hadPending) this.tailStart = this.buffer.length;
    this.transient = null;
    this.buffer += text;
    await this.flush(false);
  }

  /**
   * Record a running tool: tracked for the eventual baked summary and shown as a
   * live italic line below the streamed text until the next text replaces it.
   */
  async appendTool(toolName: string, args: Record<string, unknown>): Promise<void> {
    this.pendingTools.push({ toolName, args });
    this.transient = `_🔧 ${formatToolActivity(toolName, args)}_`;
    await this.flush(false);
  }

  /** Show a transient italic status line below the streamed text. */
  async showTransient(line: string): Promise<void> {
    this.transient = `_${line}_`;
    await this.flush(false);
  }

  /**
   * Flush the remaining text bypassing the throttle, upgrading the streaming
   * message to its final chunked form. Returns the last message id sent, or
   * null when the exchange produced no text.
   */
  async finalize(): Promise<number | null> {
    this.bakePendingTools();
    this.transient = null;

    if (this.broken) return this.finalizeBroken();

    // Chunk the finalized payload: structured (header ⊕ block ⊕ tail) when collapse is active, else
    // the markdown path. Both share the edit-first/send-rest loop below.
    const chunks: TelegramPayload[] = this.collapseActive()
      ? this.finalizeCollapseChunks()
      : convertAndSplit(this.finalizeMarkdown());

    if (chunks.length === 0) {
      await this.deleteCurrentMessage();
      return null;
    }

    let lastId = this.messageId;

    for (const [index, payload] of chunks.entries()) {
      if (index === 0 && this.messageId != null) {
        try {
          await editWithFallback(this.api, this.chatId, this.messageId, payload, this.log);
          lastId = this.messageId;
          continue;
        } catch (error) {
          this.log.warn({ err: error }, "final edit failed — sending as a new message");
        }
      }

      lastId = await sendWithFallback(this.api, this.chatId, payload, { silent: this.silent });
    }

    return lastId;
  }

  /**
   * The finalized markdown for the non-collapse path (today's behavior): header anchored above the
   * trimmed body. A header dropped mid-stream (over-limit) is already null here; a header with no
   * body still surfaces the decision label. When the body overflows into chunks the header rides the
   * first via `convertAndSplit`.
   */
  private finalizeMarkdown(): string {
    // The marker bakes a trailing blank line to separate it from the next text segment; at finalize
    // there is none, so drop the dangling whitespace.
    const body = this.buffer.trimEnd();
    const header = this.headerText();
    return header.length === 0 ? body : body.length > 0 ? `${header}\n\n${body}` : header;
  }

  /**
   * The finalized structured payload, chunked entity-safely (DLT-064, S5). At finalize `transient` is
   * null, so `composeCollapsePayload()` splits intermediate = every unit before the last, tail = the last
   * unit: the final answer text, or `[last preface + trailing tool summary]` when the turn ends on a
   * trailing tool. `header ⊕ wrapExpandable(intermediate) ⊕ tail`; an all-intensive turn with no text at
   * all collapses to the whole body being the block. Empty payload ⇒ no chunks ⇒ the caller deletes the
   * placeholder.
   */
  private finalizeCollapseChunks(): TelegramPayload[] {
    const payload = this.composeCollapsePayload();
    return payload.text.length === 0
      ? []
      : splitMessageWithEntities(payload.text, payload.entities);
  }

  /**
   * Fold the tools seen since the last text into a persistent `🔧 …` marker,
   * separated from the surrounding text by blank lines so each segment reads as
   * its own paragraph. No-op when no tools are pending. Does NOT advance `tailStart` — the
   * collapse split moves only in `appendText` when text resumes after a tool group, so a trailing-tool
   * finalize keeps the last unit's preface as the expanded tail (the last unit does not collapse).
   */
  private bakePendingTools(): void {
    if (this.pendingTools.length === 0) return;

    const summary = summarizeToolActivities(this.pendingTools);
    this.pendingTools = [];

    if (this.buffer.length > 0 && !this.buffer.endsWith("\n")) this.buffer += "\n";

    const prefix = this.buffer.length > 0 ? "\n" : "";
    this.buffer += `${prefix}_🔧 ${summary}_\n\n`;
  }

  private async flush(force: boolean): Promise<void> {
    if (this.broken) return;

    const now = Date.now();
    if (!force && now - this.lastEditAt < EDIT_THROTTLE_MS) return;

    try {
      await this.commitOverflow();

      const display = this.compose();
      if (display.length === 0 || display === this.lastRendered) return;

      // Collapse-aware payload for both the send and edit sites (DLT-064). The non-collapse path
      // reuses the `display` string already computed above — for the empty/unchanged short-circuit
      // guard and for compose()'s best-effort header-drop side effect, which has already run by
      // here — so the common case composes once and scans the buffer once, as before. The collapse
      // path rebuilds a structured payload from the buffer on every flush (the retroactive fold),
      // exactly as the header/body are today (DES-009).
      const payload = this.collapseActive()
        ? this.composeCollapsePayload()
        : toTelegramEntities(display);
      if (this.messageId == null) {
        this.messageId = await this.sendPayload(payload);
      } else {
        await editWithFallback(this.api, this.chatId, this.messageId, payload, this.log);
      }

      this.lastRendered = display;
      this.lastEditAt = now;
    } catch (error) {
      // Stop streaming entirely — finalize() falls back to plain chunked sends.
      this.broken = true;
      // Best-effort surfacing (R8): the decision took effect, but its header can no longer be rendered.
      if (this.header != null) {
        this.log.info(
          { decisionHeader: this.header },
          "decision header dropped after a render failure (best-effort surfacing)",
        );
      }
      this.log.warn({ err: error }, "streaming send/edit failed — falling back to final send");
    }
  }

  /**
   * When the buffer exceeds the edit limit, finalize every full chunk (the
   * first one in place, via edit) and keep only the tail streaming.
   */
  private async commitOverflow(): Promise<void> {
    if (this.buffer.length <= TELEGRAM_MAX_MESSAGE_LENGTH) return;

    // Split the raw buffer at paragraph boundaries (entity-safe) and keep the raw
    // tail streaming; each committed chunk is converted to an entity payload. The
    // tail must stay raw so later appends + reconversion keep working. A single
    // oversize paragraph with formatting spanning a hard split is the one edge
    // where an entity can be cut here (rare; text always survives).
    const chunks = splitMessage(this.buffer);
    this.buffer = chunks.at(-1) ?? "";

    // The committed chunks share the running collapse state at commit time — the reset happens once,
    // after the loop, so the tally isn't lost mid-commit (DLT-064, Step 9). A committed chunk carries
    // its own collapsed block when collapse is active (all-intermediate: no tail); the turn-scoped
    // header rides the first committed chunk regardless of collapse — above the block when collapsed,
    // above the chunk when inline — and is consumed there so the streaming tail doesn't duplicate it.
    // Mirrors `finalizeMarkdown`, which anchors header + body together on chunk[0].
    const collapse = this.collapseActive();
    for (const [index, chunk] of chunks.slice(0, -1).entries()) {
      const headerOnChunk = index === 0 && this.header != null;
      const body = collapse ? wrapExpandable(toTelegramEntities(chunk)) : toTelegramEntities(chunk);
      const payload = headerOnChunk
        ? concatPayloads(toTelegramEntities(this.headerText()), body)
        : body;
      if (headerOnChunk) this.header = null;

      if (this.messageId != null) {
        await editWithFallback(this.api, this.chatId, this.messageId, payload, this.log);
        this.messageId = null;
      } else {
        await sendWithFallback(this.api, this.chatId, payload, { silent: this.silent });
      }
    }

    // The committed chunks were the previous message boundary; the streaming tail is a new message,
    // so intensive-work detection resets and the tail is evaluated independently from zero (DLT-064, R9).
    // `tailStart` resets with `boundaryCount` and the `buffer` slice above — the three stay coupled so the
    // collapse split is valid in the kept tail's coordinate space (stale until new markers bake past it).
    this.boundaryCount = 0;
    this.tailStart = 0;
    this.lastRendered = "";
  }

  /**
   * The intermediate region that folds into the collapsed block (DLT-064): everything before the last
   * content-type unit — `buffer.slice(0, tailStart)`. The live tool/status line belongs to the last unit
   * and is not folded here (it rides in the tail alongside its preface), so there is no transient special
   * case. `collapseTailMd()` is its exact complement from `tailStart`.
   */
  private collapseIntermediateMd(): string {
    return this.buffer.slice(0, this.tailStart);
  }

  /**
   * The expanded tail's markdown for the collapse split (DLT-064): the last content-type unit — the
   * final text segment (the preface to the current or last tool group) from `tailStart`, with the live
   * transient line (a running tool/status) appended below it when one is showing. The preface explains
   * the tool group, so it stays visible with it rather than folding. Everything before it
   * (`collapseIntermediateMd()`) is the intermediate region that folds into the collapsed block.
   */
  private collapseTailMd(): string {
    const segment = this.buffer.slice(this.tailStart);
    if (this.transient == null) return segment;
    // Trim the segment's trailing paragraph separator so a segment ending in `\n\n` (a just-baked
    // marker's terminator) doesn't stack with the join's `\n\n`; markdown-it would normalize either
    // form, but a single blank line keeps the raw markdown legible.
    const trimmed = segment.trimEnd();
    return trimmed.length > 0 ? `${trimmed}\n\n${this.transient}` : this.transient;
  }

  /**
   * The display text to render: an optional decision header anchored above the streamed body and live
   * line. The header is recomposed on every edit so streaming never overwrites it (KD9). While a segment
   * streams (no tool/status line has settled it) the body is held; the header surfaces as the initial
   * reveal only — once anything has rendered it returns "" so flush() no-ops and preserves the visible
   * body rather than editing it back to header-only mid-stream. Because
   * `editMessageText` replaces the FULL message text (re-confirmed vs the Telegram Bot API), the
   * 4096-char limit applies to the whole composition: the transient (lowest priority) is dropped first,
   * then — best-effort (R8) — the header itself is dropped once the body grows past the limit, so a
   * long streamed response degrades gracefully rather than failing the edit. Once dropped the header
   * stays dropped (no flicker) for the rest of the exchange.
   */
  private compose(): string {
    const transient = this.transient;
    // The buffer renders only once a live line (tool/status) settles the segment; while text streams
    // (`transient == null`) it stays buffered in full — see the class doc for the rationale.
    const text = transient == null ? "" : this.buffer;

    // Body = settled text + optional live line, joined by a blank line. Shared by the header and
    // no-header paths so the header is a pure prefix over the existing composition.
    const body = text.length === 0 ? (transient ?? "") : `${text}\n\n${transient}`;

    const header = this.headerText();

    // No header ⇒ today's behavior: drop the transient when body + transient would exceed the limit.
    if (header.length === 0) {
      if (transient == null || text.length === 0) return body;
      return body.length <= TELEGRAM_MAX_MESSAGE_LENGTH ? body : text;
    }

    // With a header: anchor it above the body. Drop the transient first if the composition is too long.
    // `body` is empty only while a segment streams (`transient == null`): reveal the header on the first
    // render, then hold — return "" so flush() no-ops and preserves the visible body instead of erasing it.
    const initialHeaderReveal = this.lastRendered.length === 0 ? header : "";
    const withTransient = body.length > 0 ? `${header}\n\n${body}` : initialHeaderReveal;
    if (withTransient.length <= TELEGRAM_MAX_MESSAGE_LENGTH) return withTransient;

    if (transient != null && text.length > 0) {
      const withoutTransient = `${header}\n\n${text}`;
      if (withoutTransient.length <= TELEGRAM_MAX_MESSAGE_LENGTH) return withoutTransient;
    }

    // Header + settled text still won't fit: best-effort — drop the header for the rest of the stream.
    this.log.info(
      { decisionHeader: this.header },
      "decision header dropped — body exceeded the edit limit (best-effort surfacing)",
    );
    this.header = null;
    return body.length <= TELEGRAM_MAX_MESSAGE_LENGTH ? body : text;
  }

  /**
   * The structured payload built when collapse is active (DLT-064) — `header ⊕ wrapExpandable(intermediate)
   * ⊕ tail` — so every unit before the last folds into one collapsed block while the last unit (preface
   * text plus a live or trailing tool) stays expanded. The intermediate/tail split keys off content-type
   * transitions (`tailStart`), not paragraph breaks. The header is converted separately and prepended via
   * `concatPayloads` so it anchors above the block, never nested inside it (DES-009, S6). An empty tail
   * collapses to the whole body being the block via `concatPayloads`' empty-operand rule. Callers gate on
   * `collapseActive()`.
   */
  private composeCollapsePayload(): TelegramPayload {
    const intermediate = toTelegramEntities(this.collapseIntermediateMd());
    const tail = toTelegramEntities(this.collapseTailMd());
    const block = concatPayloads(wrapExpandable(intermediate), tail);
    return concatPayloads(toTelegramEntities(this.headerText()), block);
  }

  /** The markdown display of the decision header (the whole label + note in italics), or "" when none. */
  private headerText(): string {
    if (this.header == null) return "";
    const body =
      this.header.note.length > 0
        ? `${this.header.label} — ${this.header.note}`
        : this.header.label;
    return `_${body}_`;
  }

  /**
   * Send a fresh payload as a new message, honoring the renderer's `silent` setting so
   * streamed work-in-progress and overflow never fire partial pushes. Centralized so every
   * fresh send from the renderer shares that contract — including the collapse payload.
   */
  private async sendPayload(payload: TelegramPayload): Promise<number> {
    return sendWithFallback(this.api, this.chatId, payload, { silent: this.silent });
  }

  /**
   * Fallback path after a streaming failure: the partial message may hold
   * stale or duplicated content, so drop it and send the full remainder fresh.
   */
  private async finalizeBroken(): Promise<number | null> {
    await this.deleteCurrentMessage();

    const ids = await sendChunked(this.api, this.chatId, this.buffer.trimEnd(), {
      silent: this.silent,
    });

    return ids.at(-1) ?? null;
  }

  private async deleteCurrentMessage(): Promise<void> {
    const messageId = this.messageId;
    if (messageId == null) return;

    this.messageId = null;

    await this.api
      .deleteMessage(this.chatId, messageId)
      .catch((error) => this.log.warn({ err: error }, "streaming message cleanup failed"));
  }
}

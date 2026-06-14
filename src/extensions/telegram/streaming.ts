import type { Logger } from "../../log.ts";
import { splitMessage, TELEGRAM_MAX_MESSAGE_LENGTH } from "./chunking.ts";
import {
  editWithMarkdownFallback,
  type SendApi,
  sendChunked,
  sendWithMarkdownFallback,
} from "./sending.ts";
import { formatToolActivity, summarizeToolActivities, type ToolActivity } from "./tool-labels.ts";

export const EDIT_THROTTLE_MS = 1500;

export type StreamApi = Pick<SendApi, "sendMessage" | "editMessageText" | "deleteMessage">;

/**
 * Progressive renderer for one agent exchange. Text accumulates in a buffer and
 * is rendered a complete paragraph at a time — pi streams token-level deltas, so
 * gating on paragraph boundaries avoids the mid-word churn that editing on every
 * delta would produce. A running tool shows as a live italic line below the text;
 * when text resumes the tools that ran fold into a persistent `🔧 …` marker baked
 * into the buffer, which both records the activity and supplies the blank line
 * separating one text segment from the next. When the buffer outgrows Telegram's
 * edit limit the full chunks are finalized in place and the tail keeps streaming.
 * Edit failures degrade to the plain final-send behavior.
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

  /**
   * @param seedMessageId An existing message id to edit in place instead of
   * sending a fresh one — used to reclaim the preparation lead-in so the streamed
   * response replaces it (or deletes it, via finalize, when the exchange has no text).
   * @param silent Send every message silently (`disable_notification`). Used while
   * push notifications are on so the streamed work-in-progress and any overflow
   * chunks never fire partial pushes — the single push is forced on completion by
   * copying the finalized message (see `forceNotification`), never by these sends.
   */
  constructor(
    api: StreamApi,
    chatId: number,
    log: Logger,
    seedMessageId: number | null = null,
    silent = false,
  ) {
    this.api = api;
    this.chatId = chatId;
    this.log = log;
    this.messageId = seedMessageId;
    this.silent = silent;
  }

  async appendText(text: string): Promise<void> {
    this.bakePendingTools();
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

    // The marker bakes a trailing blank line to separate it from the next text
    // segment; at finalize there is none, so drop the dangling whitespace.
    const text = this.buffer.trimEnd();

    if (text.length === 0) {
      await this.deleteCurrentMessage();
      return null;
    }

    let lastId = this.messageId;

    for (const [index, chunk] of splitMessage(text).entries()) {
      if (index === 0 && this.messageId != null) {
        try {
          await editWithMarkdownFallback(this.api, this.chatId, this.messageId, chunk);
          lastId = this.messageId;
          continue;
        } catch (error) {
          this.log.warn({ err: error }, "final edit failed — sending as a new message");
        }
      }

      lastId = await this.sendText(chunk);
    }

    return lastId;
  }

  /**
   * Fold the tools seen since the last text into a persistent `🔧 …` marker,
   * separated from the surrounding text by blank lines so each segment reads as
   * its own paragraph. No-op when no tools are pending.
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

      if (this.messageId == null) {
        this.messageId = await this.sendText(display);
      } else {
        await editWithMarkdownFallback(this.api, this.chatId, this.messageId, display);
      }

      this.lastRendered = display;
      this.lastEditAt = now;
    } catch (error) {
      // Stop streaming entirely — finalize() falls back to plain chunked sends.
      this.broken = true;
      this.log.warn({ err: error }, "streaming send/edit failed — falling back to final send");
    }
  }

  /**
   * When the buffer exceeds the edit limit, finalize every full chunk (the
   * first one in place, via edit) and keep only the tail streaming.
   */
  private async commitOverflow(): Promise<void> {
    if (this.buffer.length <= TELEGRAM_MAX_MESSAGE_LENGTH) return;

    const chunks = splitMessage(this.buffer);
    this.buffer = chunks.at(-1) ?? "";

    for (const chunk of chunks.slice(0, -1)) {
      if (this.messageId != null) {
        await editWithMarkdownFallback(this.api, this.chatId, this.messageId, chunk);
        this.messageId = null;
      } else {
        await this.sendText(chunk);
      }
    }

    this.lastRendered = "";
  }

  /**
   * The streaming-visible text. While a live line (tool/status) is showing the
   * preceding text has settled, so the whole buffer renders beneath it. While
   * text is actively streaming only complete paragraphs render — the in-progress
   * trailing paragraph stays buffered until it closes or finalize() flushes it.
   */
  private streamableBuffer(): string {
    if (this.transient != null) return this.buffer;

    const lastBreak = this.buffer.lastIndexOf("\n\n");

    return lastBreak === -1 ? "" : this.buffer.slice(0, lastBreak);
  }

  private compose(): string {
    const text = this.streamableBuffer();

    if (this.transient == null) return text;

    if (text.length === 0) return this.transient;

    const joined = `${text}\n\n${this.transient}`;

    return joined.length <= TELEGRAM_MAX_MESSAGE_LENGTH ? joined : text;
  }

  /**
   * Send fresh text as a new message, honoring the renderer's `silent` setting so
   * streamed work-in-progress and overflow never fire partial pushes. Centralized
   * so every fresh send from the renderer shares that contract.
   */
  private async sendText(text: string): Promise<number> {
    return sendWithMarkdownFallback(this.api, this.chatId, text, { silent: this.silent });
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

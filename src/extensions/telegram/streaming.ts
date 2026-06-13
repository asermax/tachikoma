import type { Logger } from "../../log.ts";
import { splitMessage, TELEGRAM_MAX_MESSAGE_LENGTH } from "./chunking.ts";
import {
  editWithMarkdownFallback,
  type SendApi,
  sendChunked,
  sendWithMarkdownFallback,
} from "./sending.ts";

export const EDIT_THROTTLE_MS = 1500;

export type StreamApi = Pick<SendApi, "sendMessage" | "editMessageText" | "deleteMessage">;

/**
 * Progressive renderer for one agent exchange: sends a Telegram message early
 * and edits it as text streams in, throttled and skipping no-op edits. Tool
 * activity and pipeline status show as a transient italic line that the next
 * text replaces. When the accumulated text outgrows Telegram's edit limit the
 * current message is finalized in place and streaming continues in a new one.
 * Edit failures degrade to the plain final-send behavior.
 */
export class StreamRenderer {
  private readonly api: StreamApi;
  private readonly chatId: number;
  private readonly log: Logger;

  private buffer = "";
  private transient: string | null = null;
  private messageId: number | null = null;
  private lastRendered = "";
  private lastEditAt = 0;
  private broken = false;

  constructor(api: StreamApi, chatId: number, log: Logger) {
    this.api = api;
    this.chatId = chatId;
    this.log = log;
  }

  async appendText(text: string): Promise<void> {
    this.transient = null;
    this.buffer += text;
    await this.flush(false);
  }

  /** Show a transient italic line (tool marker, status) below the streamed text. */
  async showTransient(line: string): Promise<void> {
    this.transient = line;
    await this.flush(false);
  }

  /**
   * Flush the remaining text bypassing the throttle, upgrading the streaming
   * message to its final chunked form. Returns the last message id sent, or
   * null when the exchange produced no text.
   */
  async finalize(): Promise<number | null> {
    this.transient = null;

    if (this.broken) return this.finalizeBroken();

    if (this.buffer.trim().length === 0) {
      await this.deleteCurrentMessage();
      return null;
    }

    let lastId = this.messageId;

    for (const [index, chunk] of splitMessage(this.buffer).entries()) {
      if (index === 0 && this.messageId != null) {
        try {
          await editWithMarkdownFallback(this.api, this.chatId, this.messageId, chunk);
          lastId = this.messageId;
          continue;
        } catch (error) {
          this.log.warn({ err: error }, "final edit failed — sending as a new message");
        }
      }

      lastId = await sendWithMarkdownFallback(this.api, this.chatId, chunk);
    }

    return lastId;
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
        this.messageId = await sendWithMarkdownFallback(this.api, this.chatId, display);
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
        await sendWithMarkdownFallback(this.api, this.chatId, chunk);
      }
    }

    this.lastRendered = "";
  }

  private compose(): string {
    if (this.transient == null) return this.buffer;

    const marker = `_${this.transient}_`;
    if (this.buffer.length === 0) return marker;

    const joined = `${this.buffer}\n\n${marker}`;

    return joined.length <= TELEGRAM_MAX_MESSAGE_LENGTH ? joined : this.buffer;
  }

  /**
   * Fallback path after a streaming failure: the partial message may hold
   * stale or duplicated content, so drop it and send the full remainder fresh.
   */
  private async finalizeBroken(): Promise<number | null> {
    await this.deleteCurrentMessage();

    const ids = await sendChunked(this.api, this.chatId, this.buffer);

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

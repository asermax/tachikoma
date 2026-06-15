import { and, desc, eq } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { ChannelMessageStore } from "./channel.ts";
import { CHANNEL_NAME } from "./inbound.ts";
import { type ChannelMessageDirection, channelMessages } from "./schema.ts";

/**
 * Telegram-owned reply-to mapping store over the channel_messages table. Records
 * message id ↔ session mappings and resolves a replied-to message back to its
 * owning session id — the only thing reply routing needs, so the lookup reads the
 * session id straight off the mapping row rather than round-tripping the registry.
 */
export class TelegramMessageStore implements ChannelMessageStore {
  private readonly db: AppDatabase;

  constructor(db: AppDatabase) {
    this.db = db;
  }

  record(
    messageId: string,
    sessionId: number,
    direction: ChannelMessageDirection,
    text?: string,
  ): void {
    this.db
      .insert(channelMessages)
      .values({
        channel: CHANNEL_NAME,
        messageId,
        sessionId,
        direction,
        text: text ?? null,
        createdAt: new Date(),
      })
      // Re-point the mapping on conflict. `text` is only overwritten when a value
      // is supplied, so a re-record without text (e.g. an exchange re-recording an
      // id already stored as a button prompt) leaves a stored prompt intact.
      .onConflictDoUpdate({
        target: [channelMessages.channel, channelMessages.messageId],
        set: { sessionId, direction, ...(text != null ? { text } : {}) },
      })
      .run();
  }

  findSessionId(messageId: string): number | null {
    return (
      this.db
        .select({ sessionId: channelMessages.sessionId })
        .from(channelMessages)
        .where(
          and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
        )
        .get()?.sessionId ?? null
    );
  }

  /**
   * Resolves a recorded message to its owning session, stored text, and whether
   * it is that session's most recent recorded message. Returns null for an
   * unrecorded id. Centralizes the "is this reference to the live-latest message
   * or an older one worth adding context for" decision (replies, reactions,
   * button taps) so callers don't re-derive it from findSessionId + a latest scan.
   */
  findMessage(
    messageId: string,
  ): { sessionId: number; text: string | null; isLatest: boolean } | null {
    const row = this.db
      .select({ sessionId: channelMessages.sessionId, text: channelMessages.text })
      .from(channelMessages)
      .where(
        and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
      )
      .get();
    if (row == null) return null;

    return {
      sessionId: row.sessionId,
      text: row.text,
      isLatest: this.latestMessageId(row.sessionId) === messageId,
    };
  }

  /** The most recently recorded message id for a session, by created_at then row id. */
  private latestMessageId(sessionId: number): string | null {
    return (
      this.db
        .select({ messageId: channelMessages.messageId })
        .from(channelMessages)
        .where(
          and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.sessionId, sessionId)),
        )
        .orderBy(desc(channelMessages.createdAt), desc(channelMessages.id))
        .limit(1)
        .get()?.messageId ?? null
    );
  }
}

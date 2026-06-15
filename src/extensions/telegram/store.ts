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

  /** The stored text of an outgoing message (e.g. a button prompt), if any. */
  findMessageText(messageId: string): string | null {
    return (
      this.db
        .select({ text: channelMessages.text })
        .from(channelMessages)
        .where(
          and(eq(channelMessages.channel, CHANNEL_NAME), eq(channelMessages.messageId, messageId)),
        )
        .get()?.text ?? null
    );
  }

  /**
   * The most recently recorded message id for a session (any direction), by
   * created_at then row id. Used to tell whether an inbound references the
   * session's latest message (already live in the agent's context) or an older
   * one worth prepending context for.
   */
  findLatestMessageId(sessionId: number): string | null {
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

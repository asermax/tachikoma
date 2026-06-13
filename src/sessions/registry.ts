import { and, desc, eq, gt, isNull } from "drizzle-orm";

import { type SessionRecord, sessions } from "../db/core-schema.ts";
import type { AppDatabase } from "../db/index.ts";
import { type ChannelMessageDirection, channelMessages } from "../extensions/telegram/schema.ts";

export class SessionRegistry {
  private readonly db: AppDatabase;

  constructor(db: AppDatabase) {
    this.db = db;
  }

  create(channel: string, piSessionFile: string | null): SessionRecord {
    return this.db
      .insert(sessions)
      .values({ channel, piSessionFile, createdAt: new Date() })
      .returning()
      .get();
  }

  get(id: number): SessionRecord | null {
    return this.db.select().from(sessions).where(eq(sessions.id, id)).get() ?? null;
  }

  update(id: number, patch: Partial<Omit<SessionRecord, "id">>): SessionRecord {
    return this.db.update(sessions).set(patch).where(eq(sessions.id, id)).returning().get();
  }

  close(id: number): SessionRecord {
    return this.update(id, { closedAt: new Date() });
  }

  reopen(id: number): SessionRecord {
    return this.update(id, { closedAt: null, lastResumedAt: new Date() });
  }

  /** Sessions left open by a previous run (crash or restart). */
  findDangling(): SessionRecord[] {
    return this.db.select().from(sessions).where(isNull(sessions.closedAt)).all();
  }

  /** Closed sessions recent enough to be candidates for topic resumption. */
  listResumable(windowSeconds: number): SessionRecord[] {
    const cutoff = new Date(Date.now() - windowSeconds * 1000);

    return this.db
      .select()
      .from(sessions)
      .where(and(gt(sessions.closedAt, cutoff)))
      .orderBy(desc(sessions.closedAt))
      .all();
  }

  /** Record a channel message id ↔ session mapping for reply-to routing. */
  recordChannelMessage(
    channel: string,
    messageId: string,
    sessionId: number,
    direction: ChannelMessageDirection,
  ): void {
    this.db
      .insert(channelMessages)
      .values({ channel, messageId, sessionId, direction, createdAt: new Date() })
      .onConflictDoUpdate({
        target: [channelMessages.channel, channelMessages.messageId],
        set: { sessionId, direction },
      })
      .run();
  }

  /** Resolve the session that owns a recorded channel message, if any. */
  findSessionByMessageId(channel: string, messageId: string): SessionRecord | null {
    const mapping = this.db
      .select()
      .from(channelMessages)
      .where(and(eq(channelMessages.channel, channel), eq(channelMessages.messageId, messageId)))
      .get();

    if (mapping == null) return null;

    return this.get(mapping.sessionId);
  }
}

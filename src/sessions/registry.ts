import { and, asc, desc, eq, gt, isNotNull, isNull, lte } from "drizzle-orm";

import { type SessionRecord, sessions } from "../db/core-schema.ts";
import type { AppDatabase } from "../db/index.ts";

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

    // A null piSessionFile cannot be reopened on disk, so it must never be offered:
    // a chosen-but-unopenable candidate would tear down the live session first.
    return this.db
      .select()
      .from(sessions)
      .where(and(gt(sessions.closedAt, cutoff), isNotNull(sessions.piSessionFile)))
      .orderBy(desc(sessions.closedAt))
      .all();
  }

  /**
   * Closed sessions that ended in (start, end] and carry a summary, oldest-first.
   * Used to bridge a resumed session with what happened while it was closed.
   */
  listClosedBetween(start: Date, end: Date): SessionRecord[] {
    return this.db
      .select()
      .from(sessions)
      .where(
        and(gt(sessions.closedAt, start), lte(sessions.closedAt, end), isNotNull(sessions.summary)),
      )
      .orderBy(asc(sessions.closedAt))
      .all();
  }
}

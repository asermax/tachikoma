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

  /** Quarantine a session whose transcript is too corrupt to resume or post-process. */
  markErrored(id: number): SessionRecord {
    return this.update(id, { error: true });
  }

  /**
   * Sessions that never completed post-processing — left open by a crash, or closed but
   * interrupted before post-processing persisted its state. `postProcessingState` is written
   * only at the end of a successful run, so null reliably means "never finished".
   */
  findUnprocessed(): SessionRecord[] {
    return this.db.select().from(sessions).where(isNull(sessions.postProcessingState)).all();
  }

  /** Closed sessions recent enough to be candidates for topic resumption. */
  listResumable(windowSeconds: number): SessionRecord[] {
    const cutoff = new Date(Date.now() - windowSeconds * 1000);

    // A null piSessionFile cannot be reopened on disk, so it must never be offered:
    // a chosen-but-unopenable candidate would tear down the live session first.
    // An errored session is quarantined — its transcript may be too corrupt to build on.
    return this.db
      .select()
      .from(sessions)
      .where(
        and(
          gt(sessions.closedAt, cutoff),
          isNotNull(sessions.piSessionFile),
          eq(sessions.error, false),
        ),
      )
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

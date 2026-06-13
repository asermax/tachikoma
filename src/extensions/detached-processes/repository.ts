import { and, eq } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import {
  type DetachedProcessRecord,
  detachedProcesses,
  type ProcessStatus,
  STOP_REASON_AGENT_STOPPED,
} from "./schema.ts";

export interface NewDetachedProcess {
  id: string;
  name: string;
  command: string;
  cwd: string;
  pid: number;
  stdoutPath: string;
  stderrPath: string;
  memoryLimitMb: number | null;
  startedAt: Date;
}

export class ProcessRepository {
  private readonly db: AppDatabase;

  constructor(db: AppDatabase) {
    this.db = db;
  }

  create(values: NewDetachedProcess): DetachedProcessRecord {
    return this.db
      .insert(detachedProcesses)
      .values({ ...values, status: "running" })
      .returning()
      .get();
  }

  get(id: string): DetachedProcessRecord | null {
    return (
      this.db.select().from(detachedProcesses).where(eq(detachedProcesses.id, id)).get() ?? null
    );
  }

  listByStatus(status: ProcessStatus): DetachedProcessRecord[] {
    return this.db
      .select()
      .from(detachedProcesses)
      .where(eq(detachedProcesses.status, status))
      .all();
  }

  listRunning(): DetachedProcessRecord[] {
    return this.listByStatus("running");
  }

  listExited(): DetachedProcessRecord[] {
    return this.listByStatus("exited");
  }

  /**
   * Set stopReason before signalling. Caller must clearStopReason if signal
   * delivery fails, so a future natural exit is not incorrectly suppressed.
   */
  markStopInitiated(id: string): void {
    this.db
      .update(detachedProcesses)
      .set({ stopReason: STOP_REASON_AGENT_STOPPED })
      .where(eq(detachedProcesses.id, id))
      .run();
  }

  clearStopReason(id: string): void {
    this.db
      .update(detachedProcesses)
      .set({ stopReason: null })
      .where(eq(detachedProcesses.id, id))
      .run();
  }

  rename(id: string, name: string): void {
    this.db.update(detachedProcesses).set({ name }).where(eq(detachedProcesses.id, id)).run();
  }

  /** Drop an exited record. Returns true when a row was actually removed. */
  delete(id: string): boolean {
    return (
      this.db.delete(detachedProcesses).where(eq(detachedProcesses.id, id)).returning().all()
        .length > 0
    );
  }

  /**
   * Conditionally transition a record from running to exited. Returns true if
   * this caller won the race; false when another reconciler already did.
   *
   * When `stopReason` is given it is recorded as part of the same transition,
   * so OOM attribution rides along with the winning UPDATE rather than racing
   * a separate write.
   */
  reconcileToExited(
    id: string,
    exitedAt: Date,
    exitCode: number | null,
    stopReason?: string,
  ): boolean {
    return (
      this.db
        .update(detachedProcesses)
        .set(
          stopReason == null
            ? { status: "exited", exitedAt, exitCode }
            : { status: "exited", exitedAt, exitCode, stopReason },
        )
        .where(and(eq(detachedProcesses.id, id), eq(detachedProcesses.status, "running")))
        .returning()
        .all().length > 0
    );
  }
}

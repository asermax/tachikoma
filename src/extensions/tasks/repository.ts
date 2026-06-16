import { randomUUID } from "node:crypto";

import { and, desc, eq, inArray, isNotNull, lt } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import {
  type TaskDefinitionRecord,
  type TaskInstanceRecord,
  type TaskStatus,
  type TaskType,
  taskDefinitions,
  taskInstances,
} from "./schema.ts";

type TaskDefinitionInsert = typeof taskDefinitions.$inferInsert;
type TaskInstanceInsert = typeof taskInstances.$inferInsert;

// `id`, `since`, `createdAt` are stamped by createDefinition; `lastFiredAt` is
// only set by later updates, so the creation surface omits all four.
export type NewTaskDefinition = Omit<
  TaskDefinitionInsert,
  "id" | "since" | "createdAt" | "lastFiredAt"
>;

// `definitionId` is required here (null for ad-hoc instances) even though the
// column is nullable, so it is picked explicitly rather than left optional.
export type NewTaskInstance = Pick<TaskInstanceInsert, "taskType" | "prompt" | "scheduledFor"> & {
  definitionId: string | null;
};

export type DefinitionPatch = Partial<Omit<TaskDefinitionRecord, "id" | "createdAt">>;
export type InstancePatch = Partial<Omit<TaskInstanceRecord, "id" | "createdAt">>;

export interface InstanceQuery {
  status?: TaskStatus;
  taskType?: TaskType;
  definitionId?: string;
  limit?: number;
}

const ACTIVE_STATUSES: TaskStatus[] = ["pending", "running", "waiting"];

// Failed is excluded so a retry within the same period stays possible.
const PERIOD_COVERING_STATUSES: TaskStatus[] = ["pending", "running", "waiting", "completed"];

export const TERMINAL_STATUSES: TaskStatus[] = ["completed", "failed"];

export class TaskRepository {
  private readonly db: AppDatabase;
  private readonly now: () => Date;

  constructor(db: AppDatabase, now: () => Date = () => new Date()) {
    this.db = db;
    this.now = now;
  }

  // ---- definitions --------------------------------------------------------------

  createDefinition(values: NewTaskDefinition): TaskDefinitionRecord {
    const now = this.now();

    return this.db
      .insert(taskDefinitions)
      .values({
        id: randomUUID(),
        name: values.name,
        schedule: values.schedule,
        taskType: values.taskType,
        prompt: values.prompt,
        enabled: values.enabled ?? true,
        since: now,
        createdAt: now,
      })
      .returning()
      .get();
  }

  getDefinition(id: string): TaskDefinitionRecord | null {
    return this.db.select().from(taskDefinitions).where(eq(taskDefinitions.id, id)).get() ?? null;
  }

  getDefinitionByName(name: string): TaskDefinitionRecord | null {
    return (
      this.db.select().from(taskDefinitions).where(eq(taskDefinitions.name, name)).get() ?? null
    );
  }

  /** Resolve a definition by exact ID, falling back to an exact name match. */
  resolveDefinition(idOrName: string): TaskDefinitionRecord | null {
    return this.getDefinition(idOrName) ?? this.getDefinitionByName(idOrName);
  }

  deleteDefinition(id: string): boolean {
    return (
      this.db.delete(taskDefinitions).where(eq(taskDefinitions.id, id)).returning().all().length > 0
    );
  }

  listEnabledDefinitions(): TaskDefinitionRecord[] {
    return this.db.select().from(taskDefinitions).where(eq(taskDefinitions.enabled, true)).all();
  }

  listDisabledDefinitions(): TaskDefinitionRecord[] {
    return this.db.select().from(taskDefinitions).where(eq(taskDefinitions.enabled, false)).all();
  }

  /** Stamps `since` on every update (overridable) so schedule edits reset the cron anchor. */
  updateDefinition(id: string, patch: DefinitionPatch): TaskDefinitionRecord | null {
    return (
      this.db
        .update(taskDefinitions)
        .set({ since: this.now(), ...patch })
        .where(eq(taskDefinitions.id, id))
        .returning()
        .get() ?? null
    );
  }

  // ---- instances ----------------------------------------------------------------

  createInstance(values: NewTaskInstance): TaskInstanceRecord {
    const now = this.now();

    return this.db
      .insert(taskInstances)
      .values({
        id: randomUUID(),
        definitionId: values.definitionId,
        taskType: values.taskType,
        status: "pending",
        prompt: values.prompt,
        scheduledFor: values.scheduledFor,
        updatedAt: now,
        createdAt: now,
      })
      .returning()
      .get();
  }

  getInstance(id: string): TaskInstanceRecord | null {
    return this.db.select().from(taskInstances).where(eq(taskInstances.id, id)).get() ?? null;
  }

  getLatestInstanceForDefinition(definitionId: string): TaskInstanceRecord | null {
    return (
      this.db
        .select()
        .from(taskInstances)
        .where(eq(taskInstances.definitionId, definitionId))
        .orderBy(desc(taskInstances.createdAt))
        .get() ?? null
    );
  }

  getPendingInstances(taskType: TaskType): TaskInstanceRecord[] {
    return this.db
      .select()
      .from(taskInstances)
      .where(and(eq(taskInstances.status, "pending"), eq(taskInstances.taskType, taskType)))
      .all();
  }

  /** Waiting instances whose user response has arrived and are ready to resume. */
  getResumableInstances(taskType: TaskType): TaskInstanceRecord[] {
    return this.db
      .select()
      .from(taskInstances)
      .where(
        and(
          eq(taskInstances.status, "waiting"),
          eq(taskInstances.taskType, taskType),
          isNotNull(taskInstances.userResponse),
        ),
      )
      .all();
  }

  /**
   * Duplicate prevention. With `scheduledFor`, performs a period-aware check
   * (pending/running/waiting/completed instances covering that exact cron match);
   * without it, returns any pending/running/waiting instance for the definition.
   */
  getActiveInstanceForDefinition(
    definitionId: string,
    scheduledFor?: Date,
  ): TaskInstanceRecord | null {
    const conditions = [eq(taskInstances.definitionId, definitionId)];

    if (scheduledFor != null) {
      conditions.push(
        eq(taskInstances.scheduledFor, scheduledFor),
        inArray(taskInstances.status, PERIOD_COVERING_STATUSES),
      );
    } else {
      conditions.push(inArray(taskInstances.status, ACTIVE_STATUSES));
    }

    return (
      this.db
        .select()
        .from(taskInstances)
        .where(and(...conditions))
        .get() ?? null
    );
  }

  /** Waiting instances whose last update is older than `timeoutSeconds`. */
  listExpiredWaitingInstances(timeoutSeconds: number): TaskInstanceRecord[] {
    const threshold = new Date(this.now().getTime() - timeoutSeconds * 1000);

    return this.db
      .select()
      .from(taskInstances)
      .where(and(eq(taskInstances.status, "waiting"), lt(taskInstances.updatedAt, threshold)))
      .all();
  }

  /**
   * Running instances whose start (or last update, for rows that never stamped
   * `startedAt`) is older than `timeoutSeconds` — used by the stuck-running
   * sweep to free concurrency slots held by executors that never finished.
   */
  listStuckRunningInstances(timeoutSeconds: number): TaskInstanceRecord[] {
    const threshold = new Date(this.now().getTime() - timeoutSeconds * 1000);

    const running = this.db
      .select()
      .from(taskInstances)
      .where(eq(taskInstances.status, "running"))
      .all();

    return running.filter((instance) => (instance.startedAt ?? instance.updatedAt) < threshold);
  }

  /** Stamps `updatedAt` on every update unless the patch overrides it. */
  updateInstance(id: string, patch: InstancePatch): TaskInstanceRecord | null {
    return (
      this.db
        .update(taskInstances)
        .set({ updatedAt: this.now(), ...patch })
        .where(eq(taskInstances.id, id))
        .returning()
        .get() ?? null
    );
  }

  queryInstances({
    status,
    taskType,
    definitionId,
    limit = 20,
  }: InstanceQuery): TaskInstanceRecord[] {
    const conditions = [
      status != null ? eq(taskInstances.status, status) : null,
      taskType != null ? eq(taskInstances.taskType, taskType) : null,
      definitionId != null ? eq(taskInstances.definitionId, definitionId) : null,
    ].filter((condition) => condition != null);

    return this.db
      .select()
      .from(taskInstances)
      .where(conditions.length > 0 ? and(...conditions) : undefined)
      .orderBy(desc(taskInstances.createdAt))
      .limit(limit)
      .all();
  }

  /**
   * Crash recovery: running instances from a previous process are failed because
   * their executors are gone. Returns the number of instances marked.
   */
  markRunningAsFailed(reason: string): number {
    return this.db
      .update(taskInstances)
      .set({
        status: "failed",
        completedAt: this.now(),
        updatedAt: this.now(),
        result: `Task failed: ${reason}`,
      })
      .where(eq(taskInstances.status, "running"))
      .returning()
      .all().length;
  }

  /**
   * Cancel a single instance by ID: mark it `failed` with a cancellation reason.
   * A thin intention-revealing write over `updateInstance`; returns the updated
   * record or null when no row matches. The caller owns the not-found and
   * already-terminal validation, mirroring `respond_to_task`.
   */
  cancelInstance(id: string, reason: string): TaskInstanceRecord | null {
    return this.updateInstance(id, {
      status: "failed",
      completedAt: this.now(),
      result: reason,
    });
  }

  /**
   * Retention pruning for one-shot definitions that have fired and whose every
   * instance is terminal (completed/failed). The retention anchor is the latest
   * instance `completedAt`, falling back to the definition's `lastFiredAt` when
   * it produced no instances. Eligible definitions older than the window — and
   * their instances — are deleted; returns the number of definitions removed.
   */
  pruneExpiredOneShotDefinitions(retentionSeconds: number): number {
    const threshold = new Date(this.now().getTime() - retentionSeconds * 1000);

    const candidates = this.db
      .select()
      .from(taskDefinitions)
      .where(and(eq(taskDefinitions.enabled, false), isNotNull(taskDefinitions.lastFiredAt)))
      .all()
      .filter((definition) => definition.schedule.type === "once");

    let deleted = 0;

    for (const definition of candidates) {
      const instances = this.db
        .select()
        .from(taskInstances)
        .where(eq(taskInstances.definitionId, definition.id))
        .all();

      const allTerminal = instances.every((instance) =>
        TERMINAL_STATUSES.includes(instance.status),
      );

      if (!allTerminal) continue;

      const latestCompletion = instances.reduce<Date | null>((latest, instance) => {
        if (instance.completedAt == null) return latest;
        return latest == null || instance.completedAt > latest ? instance.completedAt : latest;
      }, null);

      const anchor = latestCompletion ?? definition.lastFiredAt;

      if (anchor == null || anchor >= threshold) continue;

      // Both deletes ride one transaction so a crash between them cannot orphan
      // instances under a removed definition.
      this.db.transaction((tx) => {
        tx.delete(taskInstances).where(eq(taskInstances.definitionId, definition.id)).run();
        tx.delete(taskDefinitions).where(eq(taskDefinitions.id, definition.id)).run();
      });
      deleted += 1;
    }

    return deleted;
  }
}

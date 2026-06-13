import { randomUUID } from "node:crypto";

import { and, desc, eq, inArray, isNotNull, lt } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import {
  type StoredSchedule,
  type TaskDefinitionRecord,
  type TaskInstanceRecord,
  type TaskStatus,
  type TaskType,
  taskDefinitions,
  taskInstances,
} from "./schema.ts";

export interface NewTaskDefinition {
  name: string;
  schedule: StoredSchedule;
  taskType: TaskType;
  prompt: string;
  enabled?: boolean;
}

export interface NewTaskInstance {
  definitionId: string | null;
  taskType: TaskType;
  prompt: string;
  scheduledFor: Date;
}

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
}

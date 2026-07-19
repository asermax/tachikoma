import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const TASK_TYPES = {
  session: "session",
  background: "background",
} as const;

export type TaskType = keyof typeof TASK_TYPES;

export const TASK_STATUSES = {
  pending: "pending",
  running: "running",
  waiting: "waiting",
  completed: "completed",
  failed: "failed",
} as const;

export type TaskStatus = keyof typeof TASK_STATUSES;

export interface CronSchedule {
  type: "cron";
  expression: string;
}

export interface OnceSchedule {
  type: "once";
  /** ISO instant — JSON columns cannot hold Date objects. */
  at: string;
}

export type StoredSchedule = CronSchedule | OnceSchedule;

export const taskDefinitions = sqliteTable("task_definitions", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  schedule: text("schedule", { mode: "json" }).$type<StoredSchedule>().notNull(),
  taskType: text("task_type").$type<TaskType>().notNull(),
  prompt: text("prompt").notNull(),
  // Free-text goal the task's background runs work toward; null until provided
  // or extracted from the prompt at run start.
  goal: text("goal"),
  enabled: integer("enabled", { mode: "boolean" }).notNull().default(true),
  lastFiredAt: integer("last_fired_at", { mode: "timestamp" }),
  // Stamped on every insert and update — anchors stale-cron prevention so a
  // definition never fires for occurrences that predate its latest edit.
  since: integer("since", { mode: "timestamp" }).notNull(),
  createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
});

export type TaskDefinitionRecord = typeof taskDefinitions.$inferSelect;

export const taskInstances = sqliteTable(
  "task_instances",
  {
    id: text("id").primaryKey(),
    // Nullable — ad-hoc instances have no parent definition.
    definitionId: text("definition_id").references(() => taskDefinitions.id),
    taskType: text("task_type").$type<TaskType>().notNull(),
    status: text("status").$type<TaskStatus>().notNull(),
    prompt: text("prompt").notNull(),
    // Snapshotted from the definition (or carried inline) at creation; null →
    // extracted from the prompt at run start. A snapshot, not a reference: a
    // definition goal written back after creation never reaches this instance.
    goal: text("goal"),
    scheduledFor: integer("scheduled_for", { mode: "timestamp" }).notNull(),
    startedAt: integer("started_at", { mode: "timestamp" }),
    completedAt: integer("completed_at", { mode: "timestamp" }),
    result: text("result"),
    // Set when a background run pauses on ask_user; the question surfaced to the user.
    question: text("question"),
    userResponse: text("user_response"),
    // Accumulated agent progress captured at pause time so the resumed run can
    // rebuild context — fallback path for legacy instances with no persistent session.
    resumeContext: text("resume_context"),
    // Persistent pi session file backing the background run; resumed across iterations
    // and ask_user pauses, and fed to memory extraction on completion. Null for legacy
    // instances created before persistent background sessions (they fall back to excerpt replay).
    piSessionFile: text("pi_session_file"),
    // Stamped on every update — anchors the waiting-instance expiration sweep.
    updatedAt: integer("updated_at", { mode: "timestamp" }).notNull(),
    createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
  },
  (table) => [
    index("ix_task_instances_status").on(table.status),
    index("ix_task_instances_task_type").on(table.taskType),
  ],
);

export type TaskInstanceRecord = typeof taskInstances.$inferSelect;

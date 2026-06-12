import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const PROCESS_STATUSES = {
  running: "running",
  exited: "exited",
} as const;

export type ProcessStatus = keyof typeof PROCESS_STATUSES;

export const STOP_REASON_AGENT_STOPPED = "agent_stopped";

export const detachedProcesses = sqliteTable(
  "detached_processes",
  {
    id: text("id").primaryKey(),
    name: text("name").notNull(),
    command: text("command").notNull(),
    cwd: text("cwd").notNull(),
    pid: integer("pid").notNull(),
    status: text("status").$type<ProcessStatus>().notNull(),
    exitCode: integer("exit_code"),
    stopReason: text("stop_reason"),
    stdoutPath: text("stdout_path").notNull(),
    stderrPath: text("stderr_path").notNull(),
    // Recorded only when a limit was actually applied at spawn time.
    memoryLimitMb: integer("memory_limit_mb"),
    startedAt: integer("started_at", { mode: "timestamp" }).notNull(),
    exitedAt: integer("exited_at", { mode: "timestamp" }),
  },
  (table) => [index("ix_detached_processes_status").on(table.status)],
);

export type DetachedProcessRecord = typeof detachedProcesses.$inferSelect;

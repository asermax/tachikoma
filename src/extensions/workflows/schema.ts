import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

import type { LoopState, StepSnapshot, StepStates } from "./model.ts";

export const workflowStates = sqliteTable(
  "workflow_states",
  {
    id: text("id").primaryKey(),
    skillName: text("skill_name").notNull(),
    workflowName: text("workflow_name").notNull(),
    // Composition links: set on children spawned by a composes/loop step.
    parentWorkflowId: text("parent_workflow_id"),
    parentStepId: text("parent_step_id"),
    currentStep: text("current_step"),
    stepStates: text("step_states", { mode: "json" }).$type<StepStates>().notNull(),
    definitionSnapshot: text("definition_snapshot", { mode: "json" })
      .$type<StepSnapshot[]>()
      .notNull(),
    scratchpadPath: text("scratchpad_path").notNull(),
    // Per-loop-step iteration bookkeeping; null on layers with no active loop.
    loopState: text("loop_state", { mode: "json" }).$type<LoopState>(),
    deletedAt: integer("deleted_at", { mode: "timestamp" }),
    createdAt: integer("created_at", { mode: "timestamp" }).notNull(),
    updatedAt: integer("updated_at", { mode: "timestamp" }).notNull(),
  },
  (table) => [
    index("ix_workflow_states_skill_name").on(table.skillName),
    index("ix_workflow_states_workflow_name").on(table.workflowName),
    index("ix_workflow_states_active_lookup").on(table.skillName, table.workflowName),
    index("ix_workflow_states_parent").on(table.parentWorkflowId, table.deletedAt),
  ],
);

export type WorkflowStateRecord = typeof workflowStates.$inferSelect;

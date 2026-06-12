import { and, eq, isNull, lt } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { StepSnapshot, StepStates } from "./model.ts";
import { type WorkflowStateRecord, workflowStates } from "./schema.ts";

export interface NewWorkflowState {
  id: string;
  skillName: string;
  workflowName: string;
  stepStates: StepStates;
  definitionSnapshot: StepSnapshot[];
  scratchpadPath: string;
}

/** Persistence for workflow instances. All reads exclude soft-deleted records. */
export class WorkflowStateRepository {
  private readonly db: AppDatabase;

  constructor(db: AppDatabase) {
    this.db = db;
  }

  create(state: NewWorkflowState): WorkflowStateRecord {
    const now = new Date();

    return this.db
      .insert(workflowStates)
      .values({ ...state, currentStep: null, createdAt: now, updatedAt: now })
      .returning()
      .get();
  }

  get(id: string): WorkflowStateRecord | null {
    return (
      this.db
        .select()
        .from(workflowStates)
        .where(and(eq(workflowStates.id, id), isNull(workflowStates.deletedAt)))
        .get() ?? null
    );
  }

  getActive(skillName: string, workflowName: string): WorkflowStateRecord | null {
    return (
      this.db
        .select()
        .from(workflowStates)
        .where(
          and(
            eq(workflowStates.skillName, skillName),
            eq(workflowStates.workflowName, workflowName),
            isNull(workflowStates.deletedAt),
          ),
        )
        .get() ?? null
    );
  }

  update(
    id: string,
    patch: Partial<Pick<WorkflowStateRecord, "currentStep" | "stepStates">>,
  ): WorkflowStateRecord | null {
    return (
      this.db
        .update(workflowStates)
        .set({ ...patch, updatedAt: new Date() })
        .where(and(eq(workflowStates.id, id), isNull(workflowStates.deletedAt)))
        .returning()
        .get() ?? null
    );
  }

  softDelete(id: string): boolean {
    const now = new Date();

    return (
      this.db
        .update(workflowStates)
        .set({ deletedAt: now, updatedAt: now })
        .where(and(eq(workflowStates.id, id), isNull(workflowStates.deletedAt)))
        .returning()
        .get() != null
    );
  }

  listActive(): WorkflowStateRecord[] {
    return this.db.select().from(workflowStates).where(isNull(workflowStates.deletedAt)).all();
  }

  /** Active workflows whose last update is older than the threshold. */
  listStale(thresholdMs: number): WorkflowStateRecord[] {
    const cutoff = new Date(Date.now() - thresholdMs);

    return this.db
      .select()
      .from(workflowStates)
      .where(and(isNull(workflowStates.deletedAt), lt(workflowStates.updatedAt, cutoff)))
      .all();
  }
}

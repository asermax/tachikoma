import { and, eq, inArray, isNull, lt } from "drizzle-orm";

import type { AppDatabase } from "../../db/index.ts";
import type { MutationBatch } from "./composition.ts";
import type { StepSnapshot, StepStates } from "./model.ts";
import { type WorkflowStateRecord, workflowStates } from "./schema.ts";

export interface NewWorkflowState {
  id: string;
  skillName: string;
  workflowName: string;
  stepStates: StepStates;
  definitionSnapshot: StepSnapshot[];
  scratchpadPath: string;
  parentWorkflowId?: string | null;
  parentStepId?: string | null;
}

/** Persistence for workflow instances. All reads exclude soft-deleted records. */
export class WorkflowStateRepository {
  private readonly db: AppDatabase;

  constructor(db: AppDatabase) {
    this.db = db;
  }

  create(state: NewWorkflowState): WorkflowStateRecord {
    const now = new Date();
    const parentWorkflowId = state.parentWorkflowId ?? null;

    // Composed children are exempt from the one-active-instance rule.
    if (parentWorkflowId == null && this.getActive(state.skillName, state.workflowName) != null) {
      throw new Error(
        `Active workflow state already exists for ${state.skillName}/${state.workflowName}.`,
      );
    }

    return this.db
      .insert(workflowStates)
      .values({
        ...state,
        parentWorkflowId,
        parentStepId: state.parentStepId ?? null,
        currentStep: null,
        loopState: null,
        createdAt: now,
        updatedAt: now,
      })
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

  /** The active top-level instance for a skill+workflow (children are excluded). */
  getActive(skillName: string, workflowName: string): WorkflowStateRecord | null {
    return (
      this.db
        .select()
        .from(workflowStates)
        .where(
          and(
            eq(workflowStates.skillName, skillName),
            eq(workflowStates.workflowName, workflowName),
            isNull(workflowStates.parentWorkflowId),
            isNull(workflowStates.deletedAt),
          ),
        )
        .get() ?? null
    );
  }

  /** The single active child of a layer, or null. */
  getActiveChild(parentId: string): WorkflowStateRecord | null {
    return (
      this.db
        .select()
        .from(workflowStates)
        .where(and(eq(workflowStates.parentWorkflowId, parentId), isNull(workflowStates.deletedAt)))
        .get() ?? null
    );
  }

  /** Walk the active chain from a root downward (root-first). */
  getActiveChain(rootId: string): WorkflowStateRecord[] {
    const chain: WorkflowStateRecord[] = [];
    let current = this.get(rootId);

    while (current != null) {
      chain.push(current);
      current = this.getActiveChild(current.id);
    }

    return chain;
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

  /** Top-level active instances only — nested children are never listed. */
  listActive(): WorkflowStateRecord[] {
    return this.db
      .select()
      .from(workflowStates)
      .where(and(isNull(workflowStates.parentWorkflowId), isNull(workflowStates.deletedAt)))
      .all();
  }

  /**
   * Top-level roots whose *entire active subtree* is older than the threshold —
   * an active child keeps the whole stack alive.
   */
  listStale(thresholdMs: number): WorkflowStateRecord[] {
    const cutoff = new Date(Date.now() - thresholdMs);

    const candidates = this.db
      .select()
      .from(workflowStates)
      .where(
        and(
          isNull(workflowStates.parentWorkflowId),
          isNull(workflowStates.deletedAt),
          lt(workflowStates.updatedAt, cutoff),
        ),
      )
      .all();

    return candidates.filter((root) => {
      const subtreeMax = this.getActiveChain(root.id).reduce(
        (max, layer) => (layer.updatedAt > max ? layer.updatedAt : max),
        root.updatedAt,
      );

      return subtreeMax < cutoff;
    });
  }

  /**
   * Atomically soft-delete a root and every transitive active descendant.
   * Idempotent: returns `[]` when the root is already gone.
   */
  abortCascade(rootId: string): string[] {
    return this.db.transaction((tx) => {
      const ids: string[] = [];
      let frontier = [rootId];

      while (frontier.length > 0) {
        const next: string[] = [];

        for (const id of frontier) {
          const record = tx
            .select()
            .from(workflowStates)
            .where(and(eq(workflowStates.id, id), isNull(workflowStates.deletedAt)))
            .get();

          if (record == null) continue;

          ids.push(record.id);

          for (const child of tx
            .select()
            .from(workflowStates)
            .where(
              and(eq(workflowStates.parentWorkflowId, record.id), isNull(workflowStates.deletedAt)),
            )
            .all()) {
            next.push(child.id);
          }
        }

        frontier = next;
      }

      if (ids.length > 0) {
        const now = new Date();
        tx.update(workflowStates)
          .set({ deletedAt: now, updatedAt: now })
          .where(inArray(workflowStates.id, ids))
          .run();
      }

      return ids;
    });
  }

  /** Apply a cascade's staged mutations atomically; any failure rolls back the batch. */
  applyMutationBatch(batch: MutationBatch): void {
    this.db.transaction((tx) => {
      for (const mutation of batch) {
        if (mutation.kind === "update") {
          tx.update(workflowStates)
            .set({
              stepStates: mutation.stepStates,
              currentStep: mutation.currentStep,
              updatedAt: new Date(),
              ...(mutation.loopState != null ? { loopState: mutation.loopState } : {}),
            })
            .where(eq(workflowStates.id, mutation.layerId))
            .run();
        } else if (mutation.kind === "create") {
          const now = new Date();
          tx.insert(workflowStates)
            .values({
              id: mutation.childId,
              skillName: mutation.skillName,
              workflowName: mutation.workflowName,
              parentWorkflowId: mutation.parentId,
              parentStepId: mutation.parentStepId,
              currentStep: null,
              stepStates: mutation.stepStates,
              definitionSnapshot: mutation.definitionSnapshot,
              scratchpadPath: mutation.scratchpadPath,
              loopState: null,
              createdAt: now,
              updatedAt: now,
            })
            .run();
        } else {
          const now = new Date();
          tx.update(workflowStates)
            .set({ deletedAt: now, updatedAt: now })
            .where(eq(workflowStates.id, mutation.layerId))
            .run();
        }
      }
    });
  }
}

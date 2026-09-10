import { randomUUID } from "node:crypto";
import { dirname } from "node:path";

import type { Logger } from "../../log.ts";
import {
  type BreadcrumbPart,
  type CascadeOutcome,
  type Mutation,
  type MutationBatch,
  resolveComposes,
} from "./composition.ts";
import type { StepDefinition, WorkflowDefinition } from "./loader.ts";
import { type LoopState, STEP_STATES, type StepSnapshot, type StepStates } from "./model.ts";
import type { WorkflowStateRepository } from "./repository.ts";
import type { WorkflowStateRecord } from "./schema.ts";

export type UpdateAction = "start" | "complete" | "skip";

export interface CascadeDeps {
  repository: Pick<WorkflowStateRepository, "getActiveChain">;
  findWorkflow: (skillName: string, workflowName: string) => WorkflowDefinition | null;
  log?: Logger;
}

export interface CascadeResult {
  batch: MutationBatch;
  outcome: CascadeOutcome;
  breadcrumbParts: BreadcrumbPart[];
  /** Snapshot of the deepest active layer — used to render the response. */
  deepestSnapshot: StepSnapshot[];
  scratchpadPath: string;
  /** Sub-workflow names torn down by an early completion of their parent step. */
  endedSubworkflows: string[];
}

// Belt-and-suspenders against a cycle slipping past bootstrap validation (the
// loader reads fresh, so a workflow edited mid-session could reintroduce one).
const MAX_CASCADE_DEPTH = 25;

// ---- snapshot helpers ----------------------------------------------------------

export const stepToSnapshot = (step: StepDefinition): StepSnapshot => ({
  id: step.id,
  title: step.title,
  required: step.required,
  path: dirname(step.instructionsPath),
  condition: step.condition,
  composes: step.composes,
  loop: step.loop,
});

export const getSnapshotStep = (snapshot: StepSnapshot[], stepId: string): StepSnapshot | null =>
  snapshot.find((step) => step.id === stepId) ?? null;

const findNextPendingStep = (
  stepStates: StepStates,
  snapshot: StepSnapshot[],
): StepSnapshot | null =>
  snapshot.find((step) => (stepStates[step.id] ?? STEP_STATES.pending) === "pending") ?? null;

// ---- transition validation -----------------------------------------------------

/**
 * Validate a step transition against the current states and the frozen snapshot.
 * Returns null when valid, an error message otherwise. A step with a `condition`
 * is skippable even when `required` — the condition is the agent's skip path.
 */
export const validateTransition = (
  stepStates: StepStates,
  stepId: string,
  action: UpdateAction,
  snapshot: StepSnapshot[],
): string | null => {
  const step = getSnapshotStep(snapshot, stepId);

  if (step == null) {
    return `Invalid step '${stepId}'. Valid steps: ${snapshot.map((s) => s.id).join(", ")}`;
  }

  const currentState = stepStates[stepId] ?? STEP_STATES.pending;

  if (currentState === "completed" || currentState === "skipped") {
    return `Step '${stepId}' is already ${currentState}. Cannot change a completed or skipped step.`;
  }

  if (action === "start" && currentState !== "pending") {
    return `Step '${stepId}' is already ${currentState}. Can only start a pending step.`;
  }

  if (action === "complete" && currentState !== "started") {
    return `Step '${stepId}' is ${currentState}. Must start a step before completing it.`;
  }

  if (action === "skip") {
    if (step.required && step.condition == null) {
      return `Step '${stepId}' is required and cannot be skipped.`;
    }

    if (currentState !== "pending") {
      return `Step '${stepId}' is ${currentState}. Can only skip a pending step.`;
    }
  }

  return null;
};

// ---- loop / breadcrumb helpers -------------------------------------------------

const mergeLoopState = (
  current: LoopState | null,
  stepId: string,
  items: string[],
  index: number,
): LoopState => ({ ...(current ?? {}), [stepId]: { items: [...items], index } });

/**
 * Recover an iteration child's current item from its parent's loop bookkeeping —
 * the live value lives on the parent record, not the child.
 */
const deriveCurrentItem = (
  parentLoopState: LoopState | null,
  parentStepId: string | null,
  parentCurrentItem: string | null,
): string | null => {
  if (parentLoopState != null && parentStepId != null) {
    const entry = parentLoopState[parentStepId];
    const item = entry != null && entry.index >= 0 ? entry.items[entry.index] : undefined;

    if (item != null) return item;
  }

  return parentCurrentItem;
};

/**
 * Render the active-path breadcrumb (`parent/step > child/step (item: x)`).
 * Segments for layers with no current step are omitted; only the deepest layer
 * carries the iteration item suffix. Returns "" for a single flat layer.
 */
export const renderBreadcrumb = (parts: BreadcrumbPart[]): string => {
  const segments: string[] = [];

  parts.forEach((part, index) => {
    if (part.stepId == null) return;

    const isDeepest = index === parts.length - 1;
    const suffix = isDeepest && part.item != null ? ` (item: ${part.item})` : "";

    segments.push(`${part.workflowName}/${part.stepId}${suffix}`);
  });

  return segments.join(" > ");
};

// ---- in-memory layer info ------------------------------------------------------

interface CascadeLayer {
  id: string;
  workflowName: string;
  skillName: string;
  scratchpadPath: string;
  parentWorkflowId: string | null;
  parentStepId: string | null;
  definitionSnapshot: StepSnapshot[];
  currentItem: string | null;
}

interface SpawnedChild {
  layer: CascadeLayer;
  stepStates: StepStates;
  snapshot: StepSnapshot[];
}

const spawnChild = (
  deps: CascadeDeps,
  value: string,
  parent: CascadeLayer,
  parentStepId: string,
  item: string | null = null,
): SpawnedChild => {
  let target: { skillName: string; workflowName: string };

  try {
    target = resolveComposes(value, parent.skillName);
  } catch {
    throw new Error(
      `Composition step has an invalid value: '${value}'. Abort the workflow to clean up.`,
    );
  }

  const childDef = deps.findWorkflow(target.skillName, target.workflowName);

  if (childDef == null) {
    throw new Error(
      `Composition target '${target.skillName}/${target.workflowName}' no longer exists. ` +
        "The skill may have been reloaded. Abort the workflow to clean up.",
    );
  }

  if (childDef.steps.length === 0) {
    throw new Error(
      `Composition target '${target.skillName}/${target.workflowName}' has no steps. ` +
        "Abort the workflow to clean up.",
    );
  }

  const snapshot = childDef.steps.map(stepToSnapshot);

  return {
    layer: {
      id: randomUUID(),
      workflowName: target.workflowName,
      skillName: target.skillName,
      scratchpadPath: parent.scratchpadPath,
      parentWorkflowId: parent.id,
      parentStepId,
      definitionSnapshot: snapshot,
      currentItem: item ?? parent.currentItem,
    },
    stepStates: Object.fromEntries(childDef.steps.map((s) => [s.id, STEP_STATES.pending])),
    snapshot,
  };
};

// ---- the cascade engine --------------------------------------------------------

/**
 * Apply a step transition addressed by the top-level workflow id and auto-advance
 * across composition / loop layer boundaries, staging every change as a
 * {@link MutationBatch}. The step id resolves deepest-first across the active
 * chain; completing the in-flight composition/loop step of a suspended layer
 * finishes it early (its sub-workflow is torn down). Throws on routing/validation
 * failure (state unchanged); returns the staged batch plus the outcome for
 * response rendering.
 */
export const runCascade = (
  deps: CascadeDeps,
  workflowId: string,
  step: string,
  action: UpdateAction,
  items?: string[],
): CascadeResult => {
  const chain = deps.repository.getActiveChain(workflowId);
  const root = chain[0];
  const deepest = chain[chain.length - 1];

  if (root == null || deepest == null) {
    throw new Error(`Workflow '${workflowId}' not found or no longer active.`);
  }

  if (root.parentWorkflowId != null) {
    throw new Error(
      `Workflow '${workflowId}' is a composed child. Operate on its top-level workflow instead.`,
    );
  }

  // ── resolve the step's owning layer (deepest-first) ──────────────────────────

  const deepestValidIds = deepest.definitionSnapshot.map((s) => s.id).join(", ");
  let ownerIndex = -1;
  let owner: WorkflowStateRecord | null = null;
  let stepInfo: StepSnapshot | null = null;

  for (let i = chain.length - 1; i >= 0; i -= 1) {
    const layer = chain[i];

    if (layer == null) continue;

    const found = getSnapshotStep(layer.definitionSnapshot, step);

    if (found != null) {
      ownerIndex = i;
      owner = layer;
      stepInfo = found;
      break;
    }
  }

  if (owner == null || stepInfo == null) {
    throw new Error(
      `Invalid step '${step}'. The deepest active layer is '${deepest.workflowName}'. ` +
        `Valid steps: ${deepestValidIds}.`,
    );
  }

  const isDeepest = ownerIndex === chain.length - 1;
  // The in-flight composition step: the chain link below the owner was spawned by this step.
  const isInFlight = !isDeepest && chain[ownerIndex + 1]?.parentStepId === step;
  const isLoopStep = stepInfo.loop != null;

  if (items != null && action !== "start") {
    throw new Error("items parameter is only allowed on the 'start' action.");
  }

  const routingContext = `The deepest active layer is '${deepest.workflowName}' (valid steps: ${deepestValidIds}).`;

  if (!isDeepest) {
    if (isInFlight) {
      if (action !== "complete") {
        throw new Error(
          `Step '${step}' of '${owner.workflowName}' is already started and running its sub-workflow. ` +
            `${routingContext} Complete '${step}' to finish it early.`,
        );
      }
    } else {
      const transitionError = validateTransition(
        owner.stepStates,
        step,
        action,
        owner.definitionSnapshot,
      );

      if (transitionError != null) {
        throw new Error(`${transitionError} ${routingContext}`);
      }

      throw new Error(
        `Step '${step}' belongs to '${owner.workflowName}', which is suspended while its ` +
          `sub-workflow runs. ${routingContext}`,
      );
    }
  } else {
    const transitionError = validateTransition(
      deepest.stepStates,
      step,
      action,
      deepest.definitionSnapshot,
    );

    if (transitionError != null) throw new Error(transitionError);

    if (action === "start" && items != null && !isLoopStep) {
      throw new Error("items parameter is not allowed when starting a non-loop step.");
    }
  }

  // ── mutable in-memory chain state ──────────────────────────────────────────

  const layers = new Map<string, CascadeLayer>();
  const mutableSs = new Map<string, StepStates>();
  const mutableLoop = new Map<string, LoopState | null>();
  const currentSteps = new Map<string, string | null>();
  const chainOrder: string[] = [];

  for (const state of chain) {
    layers.set(state.id, {
      id: state.id,
      workflowName: state.workflowName,
      skillName: state.skillName,
      scratchpadPath: state.scratchpadPath,
      parentWorkflowId: state.parentWorkflowId,
      parentStepId: state.parentStepId,
      definitionSnapshot: state.definitionSnapshot,
      currentItem: null,
    });
    mutableSs.set(state.id, { ...state.stepStates });
    mutableLoop.set(state.id, state.loopState ?? null);
    currentSteps.set(state.id, state.currentStep);
    chainOrder.push(state.id);
  }

  for (const state of chain) {
    if (state.parentWorkflowId == null) continue;

    const parentLayer = layers.get(state.parentWorkflowId);
    const layer = layers.get(state.id);

    if (parentLayer == null || layer == null) continue;

    layer.currentItem = deriveCurrentItem(
      mutableLoop.get(state.parentWorkflowId) ?? null,
      state.parentStepId,
      parentLayer.currentItem,
    );
  }

  const scratchpadPath = root.scratchpadPath;
  const batch: Mutation[] = [];

  const buildBreadcrumb = (): BreadcrumbPart[] =>
    chainOrder.map((id) => {
      const layer = layers.get(id);

      return {
        workflowName: layer?.workflowName ?? "",
        stepId: currentSteps.get(id) ?? null,
        item: layer?.currentItem ?? null,
      };
    });

  const guardDepth = (): void => {
    if (chainOrder.length >= MAX_CASCADE_DEPTH) {
      deps.log?.error(
        { workflowId, depth: chainOrder.length },
        "cascade depth limit hit — possible composition cycle",
      );

      throw new Error(
        "Composition nesting exceeded the safe depth (possible cycle). Abort the workflow.",
      );
    }
  };

  const descendInto = (child: SpawnedChild): void => {
    deps.log?.debug(
      {
        workflowId,
        parentId: child.layer.parentWorkflowId,
        childId: child.layer.id,
        parentStepId: child.layer.parentStepId,
        target: `${child.layer.skillName}/${child.layer.workflowName}`,
        item: child.layer.currentItem,
      },
      "spawned workflow child",
    );

    batch.push({
      kind: "create",
      childId: child.layer.id,
      parentId: child.layer.parentWorkflowId as string,
      parentStepId: child.layer.parentStepId as string,
      skillName: child.layer.skillName,
      workflowName: child.layer.workflowName,
      stepStates: { ...child.stepStates },
      definitionSnapshot: child.snapshot,
      scratchpadPath: child.layer.scratchpadPath,
    });
    layers.set(child.layer.id, child.layer);
    mutableSs.set(child.layer.id, { ...child.stepStates });
    mutableLoop.set(child.layer.id, null);
    currentSteps.set(child.layer.id, null);
    chainOrder.push(child.layer.id);
  };

  // ── apply the requested action on the owning layer ─────────────────────────

  let current = layers.get(owner.id) as CascadeLayer;
  const ss = mutableSs.get(current.id) as StepStates;
  const endedSubworkflows: string[] = [];

  // Complete a step on a suspended-owner resume (early completion) or loop
  // exhaustion: freeze its loop bookkeeping as fully iterated and stage the
  // update that persists it (`loopState` rides along only for loop steps).
  const completeStep = (layerId: string, stepId: string): void => {
    const states = mutableSs.get(layerId) as StepStates;
    states[stepId] = STEP_STATES.completed;

    const loopState = mutableLoop.get(layerId) ?? null;
    const entry = loopState?.[stepId] ?? null;
    const frozen =
      entry != null
        ? mergeLoopState(loopState, stepId, entry.items, entry.items.length)
        : loopState;

    mutableLoop.set(layerId, frozen);
    batch.push({
      kind: "update",
      layerId,
      stepStates: { ...states },
      currentStep: stepId,
      ...(frozen != null ? { loopState: frozen } : {}),
    });
  };

  // Build the result from the current chain state; every exit path funnels through it.
  const finish = (
    outcome: CascadeOutcome,
    parts: BreadcrumbPart[] = buildBreadcrumb(),
  ): CascadeResult => ({
    batch,
    outcome,
    breadcrumbParts: parts,
    deepestSnapshot: current.definitionSnapshot,
    scratchpadPath,
    endedSubworkflows,
  });

  if (!isDeepest) {
    // Early completion of the in-flight composition/loop step: tear down every
    // layer below the owner (a contiguous tail of the chain), mark the step
    // completed (loop bookkeeping frozen complete), and resume the owner's
    // auto-advance.
    for (const layer of chain.slice(ownerIndex + 1)) {
      batch.push({ kind: "softDelete", layerId: layer.id });
      endedSubworkflows.push(layer.workflowName);
    }
    chainOrder.splice(ownerIndex + 1);

    // Keep the in-memory current-step mirror in step with the staged mutation, so the
    // auto-advance halt branches read the same value the batch persists.
    currentSteps.set(current.id, step);
    completeStep(current.id, step);
    // Fall through to auto-advance on the owner.
  } else if (action === "start") {
    if (isLoopStep) {
      if (items == null) {
        throw new Error(
          "items parameter is required when starting a loop step. " +
            "Pass items=[...] (or items=[] to skip with zero iterations).",
        );
      }

      if (items.length > 0) {
        ss[step] = STEP_STATES.started;
        currentSteps.set(current.id, step);

        const loop = mergeLoopState(mutableLoop.get(current.id) ?? null, step, items, 0);
        mutableLoop.set(current.id, loop);
        batch.push({
          kind: "update",
          layerId: current.id,
          stepStates: { ...ss },
          currentStep: step,
          loopState: loop,
        });

        guardDepth();
        const child = spawnChild(deps, stepInfo.loop as string, current, step, items[0]);
        descendInto(child);
        current = child.layer;
      } else {
        ss[step] = STEP_STATES.completed;
        currentSteps.set(current.id, step);

        const loop = mergeLoopState(mutableLoop.get(current.id) ?? null, step, [], 0);
        mutableLoop.set(current.id, loop);
        batch.push({
          kind: "update",
          layerId: current.id,
          stepStates: { ...ss },
          currentStep: step,
          loopState: loop,
        });
        // Fall through to auto-advance.
      }
    } else if (stepInfo.composes == null) {
      ss[step] = STEP_STATES.started;
      currentSteps.set(current.id, step);
      batch.push({ kind: "update", layerId: current.id, stepStates: { ...ss }, currentStep: step });

      return finish({ deepestLayerId: current.id, activeStepId: step, finalizedTopLevel: false });
    } else {
      ss[step] = STEP_STATES.started;
      currentSteps.set(current.id, step);
      batch.push({ kind: "update", layerId: current.id, stepStates: { ...ss }, currentStep: step });

      guardDepth();
      const child = spawnChild(deps, stepInfo.composes, current, step);
      descendInto(child);
      current = child.layer;
    }
  } else if (action === "complete") {
    ss[step] = STEP_STATES.completed;
  } else {
    ss[step] = STEP_STATES.skipped;
  }

  // ── auto-advance loop ──────────────────────────────────────────────────────

  while (true) {
    const currentSs = mutableSs.get(current.id) as StepStates;
    const next = findNextPendingStep(currentSs, current.definitionSnapshot);

    if (next != null && next.condition != null) {
      batch.push({
        kind: "update",
        layerId: current.id,
        stepStates: { ...currentSs },
        currentStep: currentSteps.get(current.id) ?? null,
      });

      return finish({
        deepestLayerId: current.id,
        activeStepId: next.id,
        finalizedTopLevel: false,
        haltedAtConditionStep: next.id,
      });
    }

    if (next == null) {
      if (current.parentWorkflowId == null) {
        batch.push({
          kind: "update",
          layerId: current.id,
          stepStates: { ...currentSs },
          currentStep: null,
        });
        batch.push({ kind: "softDelete", layerId: current.id });

        const values = Object.values(currentSs);

        return finish(
          {
            deepestLayerId: current.id,
            activeStepId: null,
            finalizedTopLevel: true,
            completedCount: values.filter((v) => v === "completed").length,
            skippedCount: values.filter((v) => v === "skipped").length,
          },
          [],
        );
      }

      // Child layer exhausted — finalize it and advance the parent.
      batch.push({
        kind: "update",
        layerId: current.id,
        stepStates: { ...currentSs },
        currentStep: null,
      });
      batch.push({ kind: "softDelete", layerId: current.id });

      const parentId = current.parentWorkflowId;
      const parent = layers.get(parentId) as CascadeLayer;
      const parentStepId = current.parentStepId as string;
      const parentStepInfo = getSnapshotStep(
        parent.definitionSnapshot,
        parentStepId,
      ) as StepSnapshot;

      chainOrder.splice(chainOrder.indexOf(current.id), 1);

      if (parentStepInfo.loop != null) {
        const loop = mutableLoop.get(parentId) ?? {};
        const entry = loop[parentStepId] ?? { items: [], index: 0 };
        const nextIndex = entry.index + 1;

        if (nextIndex < entry.items.length) {
          const updatedLoop = mergeLoopState(
            mutableLoop.get(parentId) ?? null,
            parentStepId,
            entry.items,
            nextIndex,
          );
          mutableLoop.set(parentId, updatedLoop);
          batch.push({
            kind: "update",
            layerId: parentId,
            stepStates: { ...(mutableSs.get(parentId) as StepStates) },
            currentStep: parentStepId,
            loopState: updatedLoop,
          });

          guardDepth();
          const child = spawnChild(
            deps,
            parentStepInfo.loop,
            parent,
            parentStepId,
            entry.items[nextIndex] ?? null,
          );
          descendInto(child);
          current = child.layer;
          continue;
        }

        // Loop exhausted — complete the loop step and persist final bookkeeping.
        completeStep(parentId, parentStepId);
        current = parent;
        continue;
      }

      // Composition: mark the parent's composition step completed and resume it.
      (mutableSs.get(parentId) as StepStates)[parentStepId] = STEP_STATES.completed;
      current = parent;
      continue;
    }

    if (next.loop != null) {
      // A loop step needs an explicit start with items — halt auto-advance.
      batch.push({
        kind: "update",
        layerId: current.id,
        stepStates: { ...currentSs },
        currentStep: currentSteps.get(current.id) ?? null,
      });

      return finish({
        deepestLayerId: current.id,
        activeStepId: next.id,
        finalizedTopLevel: false,
        haltedAtLoopStep: next.id,
      });
    }

    if (next.composes != null) {
      currentSs[next.id] = STEP_STATES.started;
      currentSteps.set(current.id, next.id);
      batch.push({
        kind: "update",
        layerId: current.id,
        stepStates: { ...currentSs },
        currentStep: next.id,
      });

      guardDepth();
      const child = spawnChild(deps, next.composes, current, next.id);
      descendInto(child);
      current = child.layer;
      continue;
    }

    currentSs[next.id] = STEP_STATES.started;
    currentSteps.set(current.id, next.id);
    batch.push({
      kind: "update",
      layerId: current.id,
      stepStates: { ...currentSs },
      currentStep: next.id,
    });

    return finish({ deepestLayerId: current.id, activeStepId: next.id, finalizedTopLevel: false });
  }
};

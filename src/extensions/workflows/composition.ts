import type { StepDefinition, WorkflowDefinition } from "./loader.ts";
import type { LoopState, StepSnapshot, StepStates } from "./model.ts";

// ---- vertex addressing ---------------------------------------------------------

/** A workflow is addressed by its (skill, workflow) pair, keyed as `skill/workflow`. */
export interface WorkflowRef {
  skillName: string;
  workflowName: string;
}

export const refKey = (skillName: string, workflowName: string): string =>
  `${skillName}/${workflowName}`;

/**
 * Resolve a `composes`/`loop` frontmatter value to a {@link WorkflowRef}.
 *
 * Same-skill: `"process-note"` → `{ parentSkill, "process-note" }`.
 * Cross-skill: `"other-skill/process-note"` → `{ "other-skill", "process-note" }`.
 *
 * Throws on empty or malformed input (missing half, multiple slashes).
 */
export const resolveComposes = (value: string, parentSkillName: string): WorkflowRef => {
  const trimmed = value.trim();

  if (trimmed.length === 0) throw new Error("composition value must be a non-empty string");

  if (trimmed.includes("/")) {
    const slash = trimmed.indexOf("/");
    const skill = trimmed.slice(0, slash);
    const workflow = trimmed.slice(slash + 1);

    if (skill.length === 0 || workflow.length === 0 || workflow.includes("/")) {
      throw new Error(`Malformed composition value: '${value}'`);
    }

    return { skillName: skill, workflowName: workflow };
  }

  return { skillName: parentSkillName, workflowName: trimmed };
};

/** The single composition edge a step declares (composes or loop), or null. */
export const compositionEdge = (step: StepDefinition): string | null =>
  step.composes ?? step.loop ?? null;

// ---- graph validation (mirrors the legacy registry-load checks) ----------------

/**
 * Three-color DFS that returns each cycle found in the composition graph
 * (composes and loop edges share one graph). A self-loop is a one-vertex cycle.
 */
export const detectCycles = (workflows: Map<string, WorkflowDefinition>): string[][] => {
  const adjacency = new Map<string, string[]>();

  for (const [key, def] of workflows) {
    const targets: string[] = [];

    for (const step of def.steps) {
      const edge = compositionEdge(step);

      if (edge == null) continue;

      try {
        const target = resolveComposes(edge, def.skillName);
        const targetKey = refKey(target.skillName, target.workflowName);

        if (workflows.has(targetKey)) targets.push(targetKey);
      } catch {
        // Malformed edges are caught by reference validation, not cycle detection.
      }
    }

    adjacency.set(key, targets);
  }

  const WHITE = 0;
  const GRAY = 1;
  const BLACK = 2;
  const color = new Map<string, number>([...workflows.keys()].map((key) => [key, WHITE]));
  const stack: string[] = [];
  const cycles: string[][] = [];

  const visit = (vertex: string): void => {
    color.set(vertex, GRAY);
    stack.push(vertex);

    for (const neighbour of adjacency.get(vertex) ?? []) {
      if (color.get(neighbour) === GRAY) {
        cycles.push(stack.slice(stack.indexOf(neighbour)));
      } else if (color.get(neighbour) === WHITE) {
        visit(neighbour);
      }
    }

    stack.pop();
    color.set(vertex, BLACK);
  };

  for (const vertex of workflows.keys()) {
    if (color.get(vertex) === WHITE) visit(vertex);
  }

  return cycles;
};

export interface GraphValidation {
  /** Vertex keys (`skill/workflow`) that must not run — they would cycle or dangle. */
  rejected: Set<string>;
  /** Human-readable reasons, one per rejection, for logging. */
  warnings: string[];
}

/**
 * Reject workflows whose composition is unsafe: a step declaring both `composes`
 * and `loop`, a cycle member, or a reference to a missing/empty/rejected target.
 * Rejection cascades — a parent pointing at a rejected target is itself rejected.
 */
export const validateWorkflowGraph = (
  workflows: Map<string, WorkflowDefinition>,
): GraphValidation => {
  const rejected = new Set<string>();
  const warnings: string[] = [];

  const reject = (key: string, reason: string): void => {
    if (rejected.has(key)) return;

    rejected.add(key);
    warnings.push(`${key}: ${reason}`);
  };

  // Mutex pre-pass: a step cannot be both a composition and a loop.
  for (const [key, def] of workflows) {
    for (const step of def.steps) {
      if (step.composes != null && step.loop != null) {
        reject(key, `step '${step.id}' declares both composes and loop`);
        break;
      }
    }
  }

  for (const cycle of detectCycles(workflows)) {
    for (const member of cycle) reject(member, `composition cycle: ${cycle.join(" → ")}`);
  }

  // Fixed-point: rejecting a target may cascade to its parents.
  let changed = true;

  while (changed) {
    changed = false;

    for (const [key, def] of workflows) {
      if (rejected.has(key)) continue;

      for (const step of def.steps) {
        const edge = compositionEdge(step);

        if (edge == null) continue;

        const kind = step.loop != null ? "loop" : "composes";
        let targetKey: string;

        try {
          const target = resolveComposes(edge, def.skillName);
          targetKey = refKey(target.skillName, target.workflowName);
        } catch {
          reject(key, `step '${step.id}' has a malformed ${kind} value: '${edge}'`);
          changed = true;
          break;
        }

        const target = workflows.get(targetKey);

        if (target == null) {
          reject(key, `step '${step.id}' ${kind} target '${targetKey}' is missing`);
          changed = true;
          break;
        }

        if (rejected.has(targetKey)) {
          reject(key, `step '${step.id}' ${kind} target '${targetKey}' was itself rejected`);
          changed = true;
          break;
        }

        if (target.steps.length === 0) {
          reject(key, `step '${step.id}' ${kind} target '${targetKey}' has no steps`);
          changed = true;
          break;
        }
      }
    }
  }

  return { rejected, warnings };
};

// ---- in-memory mutations staged during a cascade -------------------------------

/** Update an existing layer's step states / current step / loop bookkeeping. */
export interface UpdateStateMutation {
  kind: "update";
  layerId: string;
  stepStates: StepStates;
  currentStep: string | null;
  /** Present only when the cascade touched loop bookkeeping for this layer. */
  loopState?: LoopState;
}

/** Spawn a composed/iterated child layer. */
export interface CreateChildMutation {
  kind: "create";
  childId: string;
  parentId: string;
  parentStepId: string;
  skillName: string;
  workflowName: string;
  stepStates: StepStates;
  definitionSnapshot: StepSnapshot[];
  scratchpadPath: string;
}

/** Soft-delete a layer (a finished child, or the finalized top-level). */
export interface SoftDeleteMutation {
  kind: "softDelete";
  layerId: string;
}

export type Mutation = UpdateStateMutation | CreateChildMutation | SoftDeleteMutation;

/** Ordered mutations applied atomically by the repository. */
export type MutationBatch = Mutation[];

// ---- cascade results -----------------------------------------------------------

export interface CascadeOutcome {
  deepestLayerId: string;
  /** The step now active (started), or null when the top-level finalized. */
  activeStepId: string | null;
  finalizedTopLevel: boolean;
  /** Tally of the finalized top-level layer's own steps (set on finalize). */
  completedCount?: number;
  skippedCount?: number;
  /** Auto-advance halted at this loop step — agent must start it with items. */
  haltedAtLoopStep?: string;
  /** Auto-advance halted at this conditional step — agent decides start/skip. */
  haltedAtConditionStep?: string;
}

/** One segment of the active-path breadcrumb. */
export interface BreadcrumbPart {
  workflowName: string;
  stepId: string | null;
  item: string | null;
}

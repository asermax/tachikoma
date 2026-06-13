export const STEP_STATES = {
  pending: "pending",
  started: "started",
  completed: "completed",
  skipped: "skipped",
} as const;

export type StepState = keyof typeof STEP_STATES;

export type StepStates = Record<string, StepState>;

/**
 * Step metadata frozen into the database when a workflow instance starts, so a
 * running workflow survives definition edits on disk.
 */
export interface StepSnapshot {
  id: string;
  title: string;
  required: boolean;
  /** Step directory; instructions.md is read from here when the step activates. */
  path: string;
  /** Natural-language predicate; when set the cascade halts so the agent decides start/skip. */
  condition: string | null;
  /** Reference to a sub-workflow run to completion when the step activates. */
  composes: string | null;
  /** Reference to a sub-workflow iterated once per agent-supplied item. */
  loop: string | null;
}

/** Per-loop-step iteration bookkeeping, persisted on the parent layer's record. */
export interface LoopEntry {
  items: string[];
  index: number;
}

/** Loop bookkeeping keyed by the loop step's id: `{ "<step_id>": { items, index } }`. */
export type LoopState = Record<string, LoopEntry>;

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
}

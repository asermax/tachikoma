import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import type { StepDefinition, WorkflowDefinition } from "./loader.ts";
import { STEP_STATES, type StepSnapshot, type StepStates } from "./model.ts";
import type { WorkflowStateRepository } from "./repository.ts";
import type { WorkflowStateRecord } from "./schema.ts";

export interface WorkflowToolDeps {
  repository: WorkflowStateRepository;
  findWorkflow: (skillName: string, workflowName: string) => WorkflowDefinition | null;
  scratchpadDir: string;
  log: Logger;
}

export const UpdateActionSchema = StringEnum(["start", "complete", "skip"] as const, {
  description: "Transition to apply to the step",
});

export const EndActionSchema = StringEnum(["complete", "abort"] as const, {
  description: "Whether the workflow ended successfully or was abandoned",
});

export type UpdateAction = Static<typeof UpdateActionSchema>;
export type EndAction = Static<typeof EndActionSchema>;

const ACTION_PAST_TENSE: Record<UpdateAction, string> = {
  start: "started",
  complete: "completed",
  skip: "skipped",
};

// ---- helpers ------------------------------------------------------------------

export const deleteScratchpad = (path: string): void => {
  try {
    rmSync(path, { force: true });
  } catch {
    // Best-effort cleanup — a leftover scratchpad never blocks the workflow.
  }
};

const stepToSnapshot = (step: StepDefinition): StepSnapshot => ({
  id: step.id,
  title: step.title,
  required: step.required,
  path: dirname(step.instructionsPath),
});

const findNextPendingStep = (
  stepStates: StepStates,
  snapshot: StepSnapshot[],
): StepSnapshot | null =>
  snapshot.find((step) => (stepStates[step.id] ?? STEP_STATES.pending) === "pending") ?? null;

const getSnapshotStep = (snapshot: StepSnapshot[], stepId: string): StepSnapshot | null =>
  snapshot.find((step) => step.id === stepId) ?? null;

const readStepInstructions = (step: StepSnapshot): string | null => {
  try {
    return readFileSync(join(step.path, "instructions.md"), "utf8");
  } catch {
    return null;
  }
};

const buildStepResponse = (step: StepSnapshot, prefix: string): string => {
  const instructions = readStepInstructions(step);

  return [
    prefix,
    ...(instructions != null ? [instructions] : []),
    `---\n*Step path: \`${step.path}\`*`,
  ].join("\n\n");
};

const formatUtc = (date: Date): string =>
  `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;

/**
 * Validate a step transition against the current states and the frozen definition.
 * Returns null when valid, an error message otherwise.
 */
export const validateTransition = (
  stepStates: StepStates,
  stepId: string,
  action: UpdateAction,
  snapshot: StepSnapshot[],
): string | null => {
  const step = getSnapshotStep(snapshot, stepId);

  if (step == null) {
    const validIds = snapshot.map((candidate) => candidate.id).join(", ");
    return `Invalid step '${stepId}'. Valid steps: ${validIds}`;
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
    if (step.required) return `Step '${stepId}' is required and cannot be skipped.`;
    if (currentState !== "pending") {
      return `Step '${stepId}' is ${currentState}. Can only skip a pending step.`;
    }
  }

  return null;
};

// ---- handlers (testable without pi) ---------------------------------------------

export const handleStartWorkflow = (
  deps: WorkflowToolDeps,
  skillName: string,
  workflowName: string,
): string => {
  const definition = deps.findWorkflow(skillName, workflowName);

  if (definition == null) {
    throw new Error(
      `Workflow '${workflowName}' not found in skill '${skillName}'. ` +
        "Check that the skill exists and contains this workflow.",
    );
  }

  if (definition.steps.length === 0) {
    throw new Error(
      `Workflow '${workflowName}' has no steps. Add step directories with instructions.md files.`,
    );
  }

  const existing = deps.repository.getActive(skillName, workflowName);

  if (existing != null) {
    throw new Error(
      `Workflow '${workflowName}' is already active for skill '${skillName}'. ` +
        `Existing workflow ID: ${existing.id}. ` +
        "Use end_workflow to complete or abort it before starting a new one.",
    );
  }

  const workflowId = randomUUID();
  const scratchpadPath = join(deps.scratchpadDir, `workflow-${workflowId}.md`);

  mkdirSync(deps.scratchpadDir, { recursive: true });
  writeFileSync(scratchpadPath, `# Workflow: ${workflowName}\n\nWorkflow ID: ${workflowId}\n`);

  const definitionSnapshot = definition.steps.map(stepToSnapshot);
  const stepStates: StepStates = Object.fromEntries(
    definition.steps.map((step) => [step.id, STEP_STATES.pending]),
  );

  try {
    deps.repository.create({
      id: workflowId,
      skillName,
      workflowName,
      stepStates,
      definitionSnapshot,
      scratchpadPath,
    });
  } catch (error) {
    deleteScratchpad(scratchpadPath);
    throw error;
  }

  const stepLines = definition.steps.map((step, index) => {
    const skipMarker = step.required ? "" : " (skippable)";
    return `${index + 1}. **${step.title}** (\`${step.id}\`)${skipMarker}`;
  });

  return [
    `Workflow started: **${workflowName}**`,
    `## Steps\n\n${stepLines.join("\n")}`,
    "## Getting Started",
    `1. Call \`update_workflow_state\` with \`workflow_id="${workflowId}", ` +
      `step="${definition.steps[0]?.id}", action="start"\` to begin the first step\n` +
      `2. Read the scratchpad file at \`${scratchpadPath}\` first, then keep it updated ` +
      `with your workflow ID (\`${workflowId}\`) and progress notes`,
    "## Progressing",
    '- Use `action="start"` to begin the first step (returns its instructions)\n' +
      '- Use `action="complete"` to finish a started step — this **auto-starts** the next ' +
      "step and returns its instructions (no separate start call needed)\n" +
      '- Use `action="skip"` to skip a skippable step — also auto-starts the next step\n' +
      "- When the last step is completed, the workflow is **auto-finalized** " +
      "(no need to call `end_workflow`)",
    "## Recovery",
    "If you lose context, call `query_workflow()` without arguments to find your workflow, " +
      "then `query_workflow(workflow_id=...)` to resume.",
  ].join("\n\n");
};

export const handleUpdateWorkflowState = (
  deps: WorkflowToolDeps,
  workflowId: string,
  stepId: string,
  action: UpdateAction,
): string => {
  const state = deps.repository.get(workflowId);

  if (state == null) throw new Error(`Workflow '${workflowId}' not found or no longer active.`);

  const transitionError = validateTransition(
    state.stepStates,
    stepId,
    action,
    state.definitionSnapshot,
  );

  if (transitionError != null) throw new Error(transitionError);

  const stepStates: StepStates = { ...state.stepStates };
  // Present after a passing validation; narrows the type without a cast.
  const step = getSnapshotStep(state.definitionSnapshot, stepId);

  if (step == null) throw new Error(`Invalid step '${stepId}'.`);

  if (action === "start") {
    stepStates[stepId] = STEP_STATES.started;
    deps.repository.update(workflowId, { stepStates, currentStep: stepId });

    return buildStepResponse(step, `Step **${step.title}** (\`${stepId}\`) started.`);
  }

  stepStates[stepId] = action === "complete" ? STEP_STATES.completed : STEP_STATES.skipped;

  const next = findNextPendingStep(stepStates, state.definitionSnapshot);

  if (next == null) {
    deps.repository.update(workflowId, { stepStates, currentStep: null });
    deps.repository.softDelete(workflowId);
    deleteScratchpad(state.scratchpadPath);

    const states = Object.values(stepStates);
    const completed = states.filter((value) => value === "completed").length;
    const skipped = states.filter((value) => value === "skipped").length;

    return (
      "Workflow complete and finalized! " +
      `All steps finished (${completed} completed, ${skipped} skipped).`
    );
  }

  stepStates[next.id] = STEP_STATES.started;
  deps.repository.update(workflowId, { stepStates, currentStep: next.id });

  return buildStepResponse(
    next,
    `Step \`${stepId}\` ${ACTION_PAST_TENSE[action]}. ` +
      `Next step **${next.title}** (\`${next.id}\`) started.`,
  );
};

const renderStateView = (state: WorkflowStateRecord): string => {
  const stepLines = state.definitionSnapshot.map(
    (step) =>
      `- **${step.title}** (\`${step.id}\`): ${state.stepStates[step.id] ?? STEP_STATES.pending}`,
  );

  return [
    "## Workflow State",
    `- **ID**: ${state.id}\n` +
      `- **Skill**: ${state.skillName}\n` +
      `- **Workflow**: ${state.workflowName}\n` +
      `- **Current Step**: ${state.currentStep ?? "none"}\n` +
      `- **Scratchpad**: \`${state.scratchpadPath}\`\n` +
      `- **Created**: ${formatUtc(state.createdAt)}\n` +
      `- **Updated**: ${formatUtc(state.updatedAt)}`,
    `### Steps\n\n${stepLines.join("\n")}`,
  ].join("\n\n");
};

export const handleQueryWorkflow = (deps: WorkflowToolDeps, workflowId?: string): string => {
  if (workflowId != null) {
    const state = deps.repository.get(workflowId);

    if (state == null) throw new Error(`Workflow '${workflowId}' not found or no longer active.`);

    return renderStateView(state);
  }

  const active = deps.repository.listActive();

  if (active.length === 0) return "No active workflows.";

  const lines = active.map(
    (state) =>
      `- **${state.workflowName}** (skill: \`${state.skillName}\`) — ID: \`${state.id}\`, ` +
      `current step: \`${state.currentStep ?? "none"}\`, started: ${formatUtc(state.createdAt)}`,
  );

  return `## Active Workflows\n\n${lines.join("\n")}`;
};

export const handleEndWorkflow = (
  deps: WorkflowToolDeps,
  workflowId: string,
  action: EndAction,
): string => {
  const state = deps.repository.get(workflowId);

  if (state == null) throw new Error(`Workflow '${workflowId}' not found or no longer active.`);

  if (!deps.repository.softDelete(workflowId)) {
    throw new Error(`Failed to end workflow '${workflowId}'.`);
  }

  deleteScratchpad(state.scratchpadPath);

  const label = action === "complete" ? "completed" : "aborted";
  return `Workflow **${state.workflowName}** ${label}. State cleaned up.`;
};

// ---- pi tool registration -------------------------------------------------------

const text = (value: string) => ({
  content: [{ type: "text" as const, text: value }],
  details: undefined,
});

const WORKFLOW_ID_PARAM = Type.String({ description: "The workflow instance ID" });

export const registerWorkflowTools = (pi: ExtensionAPI, deps: WorkflowToolDeps): void => {
  pi.registerTool({
    name: "start_workflow",
    label: "Start workflow",
    description:
      "Start a new instance of a workflow defined by a skill. Creates a tracked instance " +
      "with a unique ID and returns the step list, scratchpad path, and guidance for " +
      "progressing through the steps.",
    promptSnippet: "start_workflow: begin a tracked multi-step workflow defined by a skill",
    promptGuidelines: [
      "When a skill defines a workflow for the task at hand, drive it with start_workflow and update_workflow_state instead of improvising the steps.",
    ],
    parameters: Type.Object({
      skill_name: Type.String({ description: "Name of the skill containing the workflow" }),
      workflow_name: Type.String({ description: "Name of the workflow to start" }),
    }),

    async execute(_toolCallId, params) {
      return text(handleStartWorkflow(deps, params.skill_name, params.workflow_name));
    },
  });

  pi.registerTool({
    name: "update_workflow_state",
    label: "Update workflow state",
    description:
      "Update a workflow step's state. Validates the transition and returns step " +
      "instructions. Completing or skipping a step auto-starts the next pending step and " +
      "returns its instructions. When all steps are done, the workflow is auto-finalized " +
      "(cleaned up).",
    promptSnippet: "update_workflow_state: start, complete, or skip a workflow step",
    parameters: Type.Object({
      workflow_id: WORKFLOW_ID_PARAM,
      step: Type.String({ description: "The step identifier (directory name)" }),
      action: UpdateActionSchema,
    }),

    async execute(_toolCallId, params) {
      return text(handleUpdateWorkflowState(deps, params.workflow_id, params.step, params.action));
    },
  });

  pi.registerTool({
    name: "query_workflow",
    label: "Query workflow",
    description:
      "Query workflow state for recovery after context loss. With workflow_id, returns the " +
      "full state including all step statuses; without it, lists all active workflows.",
    promptSnippet: "query_workflow: inspect active workflows and their step states",
    parameters: Type.Object({
      workflow_id: Type.Optional(
        Type.String({ description: "Workflow instance ID. Omit to list all active workflows." }),
      ),
    }),

    async execute(_toolCallId, params) {
      return text(handleQueryWorkflow(deps, params.workflow_id));
    },
  });

  pi.registerTool({
    name: "end_workflow",
    label: "End workflow",
    description:
      "End a workflow instance. Primarily used to abort a workflow in progress — normal " +
      "completion happens automatically when the last step is completed. Removes the " +
      "workflow state and its scratchpad file.",
    promptSnippet: "end_workflow: abort or close out a workflow instance",
    parameters: Type.Object({
      workflow_id: WORKFLOW_ID_PARAM,
      action: EndActionSchema,
    }),

    async execute(_toolCallId, params) {
      return text(handleEndWorkflow(deps, params.workflow_id, params.action));
    },
  });
};

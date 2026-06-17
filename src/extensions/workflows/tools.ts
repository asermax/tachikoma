import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import {
  type CascadeDeps,
  getSnapshotStep,
  renderBreadcrumb,
  runCascade,
  stepToSnapshot,
  type UpdateAction,
} from "./cascade.ts";
import { resolveComposes } from "./composition.ts";
import type { WorkflowDefinition } from "./loader.ts";
import { STEP_STATES, type StepSnapshot } from "./model.ts";
import type { WorkflowStateRepository } from "./repository.ts";
import type { WorkflowStateRecord } from "./schema.ts";

export interface WorkflowToolDeps extends CascadeDeps {
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

export type EndAction = Static<typeof EndActionSchema>;

const ACTION_PAST_TENSE: Record<UpdateAction, string> = {
  start: "started",
  complete: "completed",
  skip: "skipped",
};

// ---- helpers ------------------------------------------------------------------

export const deleteScratchpad = (path: string, log?: Logger): void => {
  try {
    rmSync(path, { force: true });
  } catch (error) {
    log?.debug({ path, err: error }, "scratchpad cleanup failed (best-effort)");
  }
};

const readStepInstructions = (step: StepSnapshot, log: Logger): string | null => {
  try {
    return readFileSync(join(step.path, "instructions.md"), "utf8");
  } catch (error) {
    log.warn(
      { stepId: step.id, path: step.path, err: error },
      "failed to read step instructions.md",
    );

    return null;
  }
};

const buildStepResponse = (step: StepSnapshot, prefix: string, log: Logger): string => {
  const instructions = readStepInstructions(step, log);

  return [
    prefix,
    ...(instructions != null ? [instructions] : []),
    `---\n*Step path: \`${step.path}\`*`,
  ].join("\n\n");
};

const formatUtc = (date: Date): string =>
  `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;

const stepListMarkers = (step: {
  required: boolean;
  condition: string | null;
  composes: string | null;
  loop: string | null;
}): string =>
  [
    step.required ? "" : " (skippable)",
    step.condition != null ? ` (if: ${step.condition})` : "",
    step.composes != null ? ` (composes: ${step.composes})` : "",
    step.loop != null ? ` (loop: ${step.loop})` : "",
  ].join("");

// ---- handlers (testable without pi) ---------------------------------------------

export const handleStartWorkflow = (
  deps: WorkflowToolDeps,
  skillName: string,
  workflowName: string,
): string => {
  deps.log.debug({ skillName, workflowName }, "start_workflow starting");

  const definition = deps.findWorkflow(skillName, workflowName);

  if (definition == null) {
    deps.log.warn({ skillName, workflowName }, "start_workflow: workflow not found");

    throw new Error(
      `Workflow '${workflowName}' not found in skill '${skillName}'. ` +
        "Check that the skill exists and contains this workflow.",
    );
  }

  if (definition.steps.length === 0) {
    deps.log.warn({ skillName, workflowName }, "start_workflow: workflow has no steps");

    throw new Error(
      `Workflow '${workflowName}' has no steps. Add step directories with instructions.md files.`,
    );
  }

  const existing = deps.repository.getActive(skillName, workflowName);

  if (existing != null) {
    deps.log.warn(
      { skillName, workflowName, existingId: existing.id },
      "start_workflow: workflow already active",
    );

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
  const stepStates = Object.fromEntries(
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
    deps.log.warn(
      { workflowId, skillName, workflowName, err: error },
      "start_workflow: create failed",
    );

    deleteScratchpad(scratchpadPath, deps.log);
    throw error;
  }

  deps.log.info(
    { workflowId, skillName, workflowName, stepCount: definition.steps.length },
    "workflow started",
  );

  const stepLines = definition.steps.map(
    (step, index) => `${index + 1}. **${step.title}** (\`${step.id}\`)${stepListMarkers(step)}`,
  );

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
      '- A step marked `(loop: ...)` needs `action="start"` with `items=[...]`; a step marked ' +
      "`(if: ...)` halts so you can decide to start or skip it\n" +
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
  step: string,
  action: UpdateAction,
  items?: string[],
): string => {
  deps.log.debug({ workflowId, step, action }, "update_workflow_state starting");

  const result = runCascade(deps, workflowId, step, action, items);

  deps.repository.applyMutationBatch(result.batch);

  const { outcome, breadcrumbParts, deepestSnapshot, scratchpadPath } = result;
  const past = ACTION_PAST_TENSE[action];

  deps.log.info(
    {
      workflowId,
      step,
      action,
      finalized: outcome.finalizedTopLevel,
      haltedAt: outcome.haltedAtLoopStep ?? outcome.haltedAtConditionStep ?? null,
      activeStepId: outcome.activeStepId ?? null,
    },
    "workflow step transitioned",
  );

  if (outcome.finalizedTopLevel) {
    deleteScratchpad(scratchpadPath, deps.log);

    return (
      "Workflow complete and finalized! " +
      `All steps finished (${outcome.completedCount} completed, ${outcome.skippedCount} skipped).`
    );
  }

  if (outcome.haltedAtLoopStep != null) {
    const halted = getSnapshotStep(deepestSnapshot, outcome.haltedAtLoopStep);
    const title = halted?.title ?? outcome.haltedAtLoopStep;

    return (
      `Step \`${step}\` ${past}.\n\n` +
      `The next step **${title}** (\`${outcome.haltedAtLoopStep}\`) is a loop step. ` +
      `Call \`update_workflow_state(workflow_id="${workflowId}", ` +
      `step="${outcome.haltedAtLoopStep}", action="start", items=[...])\` to begin iterating, ` +
      "or `items=[]` to skip with zero iterations."
    );
  }

  if (outcome.haltedAtConditionStep != null) {
    const halted = getSnapshotStep(deepestSnapshot, outcome.haltedAtConditionStep);
    const title = halted?.title ?? outcome.haltedAtConditionStep;

    return (
      `Step \`${step}\` ${past}.\n\n` +
      `The next step **${title}** (\`${outcome.haltedAtConditionStep}\`) has a condition to evaluate:\n\n` +
      `**Condition**: ${halted?.condition}\n\n` +
      "Evaluate this condition based on the current context.\n" +
      `- If it passes: call \`update_workflow_state(workflow_id="${workflowId}", ` +
      `step="${outcome.haltedAtConditionStep}", action="start")\`\n` +
      `- If it does not pass: call \`update_workflow_state(workflow_id="${workflowId}", ` +
      `step="${outcome.haltedAtConditionStep}", action="skip")\``
    );
  }

  const activeStepId = outcome.activeStepId;

  if (activeStepId == null) {
    deps.log.error({ workflowId, step, action }, "cascade produced no active step");

    throw new Error("Cascade produced no active step.");
  }

  const stepInfo = getSnapshotStep(deepestSnapshot, activeStepId);

  if (stepInfo == null) {
    deps.log.error({ workflowId, activeStepId }, "active step missing from deepest snapshot");

    throw new Error(`Active step '${activeStepId}' missing from snapshot.`);
  }

  const prefix =
    action === "start" && activeStepId === step
      ? `Step **${stepInfo.title}** (\`${activeStepId}\`) started.`
      : `Step \`${step}\` ${past}. Next step **${stepInfo.title}** (\`${activeStepId}\`) started.`;

  const response = buildStepResponse(stepInfo, prefix, deps.log);
  const breadcrumb = breadcrumbParts.length > 1 ? renderBreadcrumb(breadcrumbParts) : "";

  return breadcrumb.length > 0 ? `${breadcrumb}\n\n${response}` : response;
};

// ---- query rendering -----------------------------------------------------------

const renderLoopStepBlocks = (state: WorkflowStateRecord): string => {
  if (state.loopState == null) return "";

  const blocks = Object.entries(state.loopState).map(([stepId, entry]) => {
    const step = getSnapshotStep(state.definitionSnapshot, stepId);
    const title = step?.title ?? stepId;
    const count = entry.items.length;

    const itemsLine =
      count === 0
        ? "Items (0): (none) — completed with zero iterations"
        : `Items (${count}): ${entry.items.map((i) => `\`${i}\``).join(", ")}`;

    const iterationLine =
      entry.index >= count
        ? `Current iteration: ${count} / ${count} (complete)`
        : `Current iteration: ${entry.index + 1} / ${count}\n- Current item: \`${entry.items[entry.index]}\``;

    return `### Loop step: ${title} (\`${stepId}\`)\n\n- ${itemsLine}\n- ${iterationLine}`;
  });

  return blocks.join("\n\n");
};

const renderStateView = (state: WorkflowStateRecord): string => {
  const stepLines = state.definitionSnapshot.map(
    (step) =>
      `- **${step.title}** (\`${step.id}\`): ${state.stepStates[step.id] ?? STEP_STATES.pending}`,
  );

  const loopBlocks = renderLoopStepBlocks(state);

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
    ...(loopBlocks.length > 0 ? [loopBlocks] : []),
  ].join("\n\n");
};

interface Corruption {
  workflowName: string;
  stepId: string;
  target: string;
}

/** Active composition steps whose target is no longer registered (skill reloaded). */
const detectCorruptedTargets = (
  deps: WorkflowToolDeps,
  chain: WorkflowStateRecord[],
): Corruption[] => {
  const corrupted: Corruption[] = [];

  for (const layer of chain) {
    for (const stepDef of layer.definitionSnapshot) {
      if (stepDef.composes == null) continue;
      if (layer.stepStates[stepDef.id] !== STEP_STATES.started) continue;

      try {
        const target = resolveComposes(stepDef.composes, layer.skillName);

        if (deps.findWorkflow(target.skillName, target.workflowName) == null) {
          deps.log.warn(
            {
              workflowId: layer.id,
              stepId: stepDef.id,
              target: `${target.skillName}/${target.workflowName}`,
            },
            "workflow composition target corrupted",
          );

          corrupted.push({
            workflowName: layer.workflowName,
            stepId: stepDef.id,
            target: `${target.skillName}/${target.workflowName}`,
          });
        }
      } catch (error) {
        deps.log.warn(
          { workflowId: layer.id, stepId: stepDef.id, target: stepDef.composes, err: error },
          "workflow composition target corrupted",
        );

        corrupted.push({
          workflowName: layer.workflowName,
          stepId: stepDef.id,
          target: stepDef.composes,
        });
      }
    }
  }

  return corrupted;
};

const formatCorruption = (corrupted: Corruption[]): string =>
  [
    "> ⚠️  **Workflow definition corruption detected.**",
    ">",
    ...corrupted.map(
      (c) =>
        `> - Step \`${c.workflowName}/${c.stepId}\` references \`${c.target}\`, no longer registered.`,
    ),
    ">",
    "> The active workflow cannot proceed safely. Abort it with `end_workflow(action='abort')`.",
  ].join("\n");

const deriveChainItems = (chain: WorkflowStateRecord[]): (string | null)[] => {
  const items: (string | null)[] = [];

  chain.forEach((layer, index) => {
    const parent = chain[index - 1];

    if (index === 0 || parent == null) {
      items.push(null);
      return;
    }

    const entry = layer.parentStepId != null ? parent.loopState?.[layer.parentStepId] : undefined;
    const item = entry != null && entry.index >= 0 ? entry.items[entry.index] : undefined;

    items.push(item ?? items[index - 1] ?? null);
  });

  return items;
};

export const handleQueryWorkflow = (deps: WorkflowToolDeps, workflowId?: string): string => {
  if (workflowId == null) {
    const active = deps.repository.listActive();

    if (active.length === 0) return "No active workflows.";

    const lines = active.map(
      (state) =>
        `- **${state.workflowName}** (skill: \`${state.skillName}\`) — ID: \`${state.id}\`, ` +
        `current step: \`${state.currentStep ?? "none"}\`, started: ${formatUtc(state.createdAt)}`,
    );

    return `## Active Workflows\n\n${lines.join("\n")}`;
  }

  const chain = deps.repository.getActiveChain(workflowId);
  const head = chain[0];

  if (head == null) {
    deps.log.warn({ workflowId }, "query_workflow: workflow not found or inactive");

    throw new Error(`Workflow '${workflowId}' not found or no longer active.`);
  }

  if (head.parentWorkflowId != null) {
    return [
      renderStateView(head),
      "> This is a composed child. Access via the top-level workflow for the full nested view.",
      `> Parent workflow ID: \`${head.parentWorkflowId}\``,
    ].join("\n\n");
  }

  const corrupted = detectCorruptedTargets(deps, chain);
  const parts: string[] = [];

  if (chain.length > 1) {
    const items = deriveChainItems(chain);
    const breadcrumb = renderBreadcrumb(
      chain.map((layer, index) => ({
        workflowName: layer.workflowName,
        stepId: layer.currentStep,
        item: items[index] ?? null,
      })),
    );

    if (breadcrumb.length > 0) parts.push(`> ${breadcrumb}`);
  }

  parts.push(renderStateView(head));

  for (const child of chain.slice(1)) {
    const childSteps = child.definitionSnapshot.map(
      (step) =>
        `  - **${step.title}** (\`${step.id}\`): ${child.stepStates[step.id] ?? STEP_STATES.pending}`,
    );
    const childLoops = renderLoopStepBlocks(child);

    parts.push(
      `### Active Child: ${child.workflowName}\n\n` +
        `- **ID**: ${child.id}\n- **Current Step**: ${child.currentStep ?? "none"}\n\n` +
        `#### Steps\n\n${childSteps.join("\n")}` +
        (childLoops.length > 0 ? `\n\n${childLoops}` : ""),
    );
  }

  const view = parts.join("\n\n");

  return corrupted.length > 0 ? `${formatCorruption(corrupted)}\n\n${view}` : view;
};

export const handleEndWorkflow = (
  deps: WorkflowToolDeps,
  workflowId: string,
  action: EndAction,
): string => {
  deps.log.debug({ workflowId, action }, "end_workflow starting");

  const state = deps.repository.get(workflowId);

  if (state == null) {
    deps.log.warn({ workflowId, action }, "end_workflow: workflow not found or inactive");

    throw new Error(`Workflow '${workflowId}' not found or no longer active.`);
  }

  if (state.parentWorkflowId != null) {
    deps.log.warn(
      { workflowId, action, parentWorkflowId: state.parentWorkflowId },
      "end_workflow: refused on composed child",
    );

    throw new Error(
      `Workflow '${workflowId}' is a composed child. End its top-level workflow instead.`,
    );
  }

  const ids = deps.repository.abortCascade(workflowId);

  if (ids.length === 0) {
    deps.log.error({ workflowId, action }, "end_workflow: abortCascade removed no records");

    throw new Error(`Failed to end workflow '${workflowId}'.`);
  }

  deleteScratchpad(state.scratchpadPath, deps.log);

  deps.log.info({ workflowId, action, recordsCleaned: ids.length }, "workflow ended");

  const label = action === "complete" ? "completed" : "aborted";
  const count = ids.length > 1 ? ` (${ids.length} records cleaned up)` : "";

  return `Workflow **${state.workflowName}** ${label}.${count} State cleaned up.`;
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
      "instructions. Completing or skipping a step auto-starts the next pending step. " +
      "A loop step requires `items` on the start action (each item runs the loop target " +
      "once); a step with a condition halts auto-advance so you decide start vs skip. " +
      "When all steps are done, the workflow is auto-finalized (cleaned up).",
    promptSnippet: "update_workflow_state: start, complete, or skip a workflow step",
    parameters: Type.Object({
      workflow_id: WORKFLOW_ID_PARAM,
      step: Type.String({ description: "The step identifier (directory name)" }),
      action: UpdateActionSchema,
      items: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Required when starting a loop step: opaque references the loop target iterates " +
            "over, one run per item. Pass [] to skip a loop step with zero iterations. " +
            "Rejected on non-loop steps and on non-start actions.",
        }),
      ),
    }),

    async execute(_toolCallId, params) {
      return text(
        handleUpdateWorkflowState(
          deps,
          params.workflow_id,
          params.step,
          params.action,
          params.items,
        ),
      );
    },
  });

  pi.registerTool({
    name: "query_workflow",
    label: "Query workflow",
    description:
      "Query workflow state for recovery after context loss. With workflow_id, returns the " +
      "full state including the active composed/loop child path; without it, lists all active " +
      "top-level workflows.",
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
      "completion happens automatically when the last step is completed. Aborting a " +
      "top-level workflow tears down any composed/loop children too. Removes the workflow " +
      "state and its scratchpad file.",
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

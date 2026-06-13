import { StringEnum } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { SideRunner } from "../../agent/side-run.ts";
import type { Delivery } from "../../channels/types.ts";
import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";
import type { TaskInstanceRecord } from "./schema.ts";

export const TaskEvaluationSchema = Type.Object({
  status: StringEnum(["complete", "continue", "error"] as const),
  reason: Type.String(),
});

export type TaskEvaluation = Static<typeof TaskEvaluationSchema>;

export interface TaskNotification {
  source: string;
  instanceId: string;
  status: "completed" | "failed";
  message: string;
}

export type BackgroundSide = Pick<SideRunner, "run" | "classify">;

export interface ExecutorDeps {
  repository: TaskRepository;
  side: BackgroundSide;
  deliver: (delivery: Delivery) => void;
  notify: (notification: TaskNotification) => void;
  maxIterations: number;
  timezone: string | undefined;
  now: () => Date;
  log: Logger;
}

const BACKGROUND_TOOLS = ["read", "bash", "edit", "write", "grep", "find", "ls"];

const RESPONSE_EXCERPT_CHARS = 4000;

const BACKGROUND_SYSTEM_PROMPT = `You are a background task agent. You are executing a scheduled task autonomously. Complete the task described below. Your work will be saved automatically.

You are operating without direct user interaction. Work through the task methodically, and when you believe the task is complete, provide a clear summary of what was accomplished. Your final summary is delivered to the user when the task completes — failure notices are sent automatically.`;

const EVALUATOR_SYSTEM = `You are a task completion evaluator for a background task agent. Your ONLY job is to classify the agent's current workflow state using the ordered rules below.

You are a CLASSIFIER, not a reviewer. You MUST NOT:
- Perform any qualitative analysis of the agent's output (correctness, thoroughness, style, usefulness, depth of investigation)
- Offer corrective feedback, suggestions, improvements, or opinions about what the agent should have done differently
- Apply any criteria beyond the rules listed below

The "reason" field explains why you chose this classification and must describe observable facts about what the agent said or did — never advice, critique, or evaluation directed at the agent.

Classification rules (evaluate in order, use the first match):

1. Blocking error: Did the agent report an unrecoverable error, get stuck in a loop, or fail repeatedly without making progress?
   -> {"status": "error", "reason": "Factual description of the blocking signal the agent reported"}

2. Workflow complete: Did the agent execute the requested actions and announce completion, summarize results, or produce final output? Classify as complete even if the agent mentions optional follow-up actions it could take — completion announcements take precedence over hypothetical next steps.
   -> {"status": "complete", "reason": "Brief factual summary of what the agent reported accomplishing"}

3. Mid-workflow: Is the agent still working — it announced next steps but hasn't executed them yet, or it's partway through a multi-step process?
   -> {"status": "continue", "reason": "What the agent said it would do next but has not yet executed"}`;

const buildSystemPrompt = (now: Date, timezone: string | undefined): string => {
  const formatted = now.toLocaleString("en-US", {
    timeZone: timezone,
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const zone = timezone != null ? ` ${timezone}` : "";

  return `Current date and time: ${formatted}${zone}\n\n${BACKGROUND_SYSTEM_PROMPT}`;
};

// Side runs are ephemeral (no session continuity), so each continuation
// carries the task, the previous progress, and the evaluator's observation.
const buildContinuationPrompt = (taskPrompt: string, previousResponse: string, reason: string) =>
  `You are resuming a background task that is not finished yet.

<task>
${taskPrompt}
</task>

<previous-progress>
${previousResponse.slice(0, RESPONSE_EXCERPT_CHARS)}
</previous-progress>

An evaluator observed: ${reason}

Continue working on the task from where you left off.`;

export const evaluateCompletion = async (
  side: Pick<SideRunner, "classify">,
  taskPrompt: string,
  agentResponse: string,
  log: Logger,
): Promise<TaskEvaluation> => {
  try {
    return await side.classify({
      system: EVALUATOR_SYSTEM,
      user: `<task-definition>\n${taskPrompt}\n</task-definition>\n\n<agent-response>\n${agentResponse.slice(0, RESPONSE_EXCERPT_CHARS)}\n</agent-response>`,
      schema: TaskEvaluationSchema,
    });
  } catch (error) {
    // The evaluator is best-effort: a failure must never abort the run.
    log.warn({ err: error }, "evaluator failed — continuing");
    return { status: "continue", reason: "Evaluator failed, continuing" };
  }
};

/** Execute one background instance through the iterative evaluator loop. */
export const executeBackgroundInstance = async (
  deps: ExecutorDeps,
  instance: TaskInstanceRecord,
): Promise<void> => {
  const { repository, side, deliver, notify, maxIterations, timezone, now, log } = deps;

  const definition =
    instance.definitionId != null ? repository.getDefinition(instance.definitionId) : null;
  const source =
    definition != null
      ? `Background task: ${definition.name}`
      : `Background task: ${instance.prompt.slice(0, 100)}`;

  const fail = (reason: string, noticeMessage: string): void => {
    repository.updateInstance(instance.id, {
      status: "failed",
      completedAt: now(),
      result: reason,
    });
    deliver({ text: `❌ ${source} — ${noticeMessage}`, gate: "idle" });
    notify({ source, instanceId: instance.id, status: "failed", message: noticeMessage });
    log.warn({ instanceId: instance.id, reason }, "background task failed");
  };

  repository.updateInstance(instance.id, { status: "running", startedAt: now() });
  log.info({ instanceId: instance.id }, "executing background task instance");

  try {
    const system = buildSystemPrompt(now(), timezone);
    let prompt = instance.prompt;

    const notifyTool = defineTool({
      name: "notify_user",
      label: "Notify User",
      description:
        "Send the user a notification about something important discovered during this background task.",
      parameters: Type.Object({
        text: Type.String({ description: "Notification text" }),
        severity: StringEnum(["info", "warning", "urgent"] as const, { default: "info" }),
      }),
      execute: async (_id, params) => {
        deliver({
          text: `${params.severity === "urgent" ? "🚨 " : ""}${source}: ${params.text}`,
          gate: params.severity === "urgent" ? "immediate" : "idle",
          priority: params.severity === "urgent" ? 10 : 0,
        });

        return { content: [{ type: "text", text: "Notification queued." }], details: {} };
      },
    });

    for (let iteration = 1; iteration <= maxIterations; iteration += 1) {
      const { text } = await side.run({
        system,
        prompt,
        tools: BACKGROUND_TOOLS,
        customTools: [notifyTool],
        tier: "processor",
      });

      const evaluation = await evaluateCompletion(side, instance.prompt, text, log);

      log.debug(
        { instanceId: instance.id, iteration, status: evaluation.status },
        "evaluator result",
      );

      if (evaluation.status === "complete") {
        repository.updateInstance(instance.id, {
          status: "completed",
          completedAt: now(),
          result: evaluation.reason,
        });
        deliver({ text: `✅ ${source} — completed.\n\n${text}`, gate: "idle" });
        notify({
          source,
          instanceId: instance.id,
          status: "completed",
          message: evaluation.reason,
        });
        log.info({ instanceId: instance.id }, "background task completed");
        return;
      }

      if (evaluation.status === "error") {
        fail(`Agent stuck: ${evaluation.reason}`, `Task failed: ${evaluation.reason}`);
        return;
      }

      prompt = buildContinuationPrompt(instance.prompt, text, evaluation.reason);
    }

    fail(
      `Max iterations (${maxIterations}) reached without completion`,
      `Task failed: reached max iterations (${maxIterations})`,
    );
  } catch (error) {
    log.error({ instanceId: instance.id, err: error }, "background task errored");
    fail(`Task failed: ${error}`, `Task failed with error: ${error}`);
  }
};

/**
 * Tick-driven dispatcher for background instances. In-flight executions are
 * tracked across ticks so a slow run is never dispatched twice.
 */
export class BackgroundRunner {
  private readonly deps: ExecutorDeps;
  private readonly inFlight = new Map<string, Promise<void>>();

  constructor(deps: ExecutorDeps) {
    this.deps = deps;
  }

  tick(): void {
    for (const instance of this.deps.repository.getPendingInstances("background")) {
      if (this.inFlight.has(instance.id)) continue;

      const run = executeBackgroundInstance(this.deps, instance)
        .catch((error) =>
          this.deps.log.error(
            { instanceId: instance.id, err: error },
            "background executor crashed",
          ),
        )
        .finally(() => this.inFlight.delete(instance.id));

      this.inFlight.set(instance.id, run);
    }
  }

  /** Await in-flight executions (tests and shutdown). */
  async drain(): Promise<void> {
    await Promise.allSettled(this.inFlight.values());
  }
}

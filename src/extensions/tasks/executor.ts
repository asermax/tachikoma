import { StringEnum } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import { buildBackgroundSystemPrompt } from "../../agent/prompts.ts";
import { lastAssistantText, type SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { PostProcessorContext } from "../api.ts";
import { NOTIFY_EVENT, type NotifyPayload, SEVERITIES } from "../notifications/payload.ts";
import type { TaskRepository } from "./repository.ts";
import type { TaskInstanceRecord } from "./schema.ts";

export const TaskEvaluationSchema = Type.Object({
  status: StringEnum(["complete", "continue", "error"] as const),
  reason: Type.String(),
});

export type TaskEvaluation = Static<typeof TaskEvaluationSchema>;

// Structured output of a run-start goal extraction: a single free-text goal derived
// from the task prompt. See GOAL_EXTRACTION_SYSTEM for the recommended prose structure.
export const ExtractedGoalSchema = Type.Object({
  goal: Type.String(),
});

export type ExtractedGoal = Static<typeof ExtractedGoalSchema>;

export type NotifyEmitter = (event: string, payload: NotifyPayload) => void;

export type BackgroundSide = Pick<SideRunner, "classify" | "openBackgroundSession">;

export interface ExecutorDeps {
  repository: TaskRepository;
  side: BackgroundSide;
  /** Emit a `"notify"` event so background-task notices flow through the notifications router. */
  emit: NotifyEmitter;
  /** Run the registered post-processors after a task completes (workspace persistence). */
  runPostProcessors: (context: PostProcessorContext) => Promise<void>;
  maxIterations: number;
  maxConcurrent: number;
  timezone: string | undefined;
  now: () => Date;
  log: Logger;
}

const RESPONSE_EXCERPT_CHARS = 4000;

// R15's "empty, trivial, or malformed" threshold: an extracted goal shorter than this
// (after trim) is treated as unusable — extraction returns null, nothing is persisted, and
// the next run retries. A tunable heuristic; 50 chars rules out bare "do the task"-style
// goals while comfortably admitting a one-sentence end state plus its check.
export const MIN_GOAL_LENGTH = 50;

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

// Formats the timezone-aware header here (this extension owns the configured timezone) and hands
// the prompt text composition to the shared prompt module.
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

  return buildBackgroundSystemPrompt({ dateHeader: `${formatted}${zone}` });
};

// The persistent session retains its own history, so a continuation is a short nudge
// carrying only the evaluator's observation — no excerpt replay of the previous turn.
const buildContinuationPrompt = (reason: string) =>
  `Continue working on the task. Evaluator note: ${reason}`;

// Derives a free-text completion goal from a task prompt. The goal anchors what "done"
// means for the run; the recommended prose structure (end state + stated check +
// invariants) is enforced by instruction here and on the other agent-facing surfaces, not
// by a parsed schema. A vague prompt yields an empty goal, which the heuristic rejects.
const GOAL_EXTRACTION_SYSTEM = `You derive a concise completion goal for an autonomous background task from its task prompt.

The goal anchors what "done" means. Express it as:
- A measurable END STATE: the concrete outcome the task produces.
- A STATED CHECK: how the agent can verify that end state is reached — an artifact, a measurement, or another observable outcome (not a bare assertion).
- INVARIANTS: constraints on what must NOT change while reaching it (for example, "do not break existing behavior" or "do not modify files outside the project").

Rules:
- State the goal as a short paragraph or a few lines in the agent's own working terms — NOT a list of steps, and NOT meta-commentary about the task.
- Be specific and genuinely checkable; reject trivially satisfied goals like "do the task" or "work on it until finished".
- Derive ONLY from the task prompt; do not invent scope the prompt does not imply.
- If the prompt is too vague to derive a meaningful, checkable goal, return an empty goal string.`;

// Goal-only surfacing in the opening turn: wraps the task prompt with the goal when one is
// present, and leaves it bare when there is none. The declare-via-update_goal instruction
// is appended in a later batch (the tool does not exist yet); for now the agent simply sees
// its goal alongside the task.
const buildOpeningPrompt = (taskPrompt: string, goal: string | null): string =>
  goal == null ? taskPrompt : `<task>\n${taskPrompt}\n</task>\n\n<goal>\n${goal}\n</goal>`;

// Legacy fallback for instances created before persistent background sessions: a
// paused-then-answered run with no session file is resumed by replaying the captured
// progress, the question it asked, and the user's reply into a fresh prompt.
const buildResumePrompt = (
  taskPrompt: string,
  resumeContext: string,
  question: string,
  userResponse: string,
) =>
  `You are resuming a background task that paused to ask the user a question.

<task>
${taskPrompt}
</task>

<previous-progress>
${resumeContext.slice(0, RESPONSE_EXCERPT_CHARS)}
</previous-progress>

You asked the user: ${question}

The user replied: ${userResponse}

Continue working on the task from where you left off, using the user's reply. Do not ask the same question again.`;

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

/**
 * Derive a completion goal from a task prompt via structured extraction. Returns the goal
 * prose when extraction succeeds and it clears the trim + minimum-length heuristic, or
 * `null` on a throw or an unusable result. A `null` result means the run proceeds on the
 * task-prompt basis and persists nothing — every later run retries until a usable goal is
 * extracted (R3/R14/R15). Lazy, marker-free migration per DES-006: a non-null goal is the
 * done signal, with no backfill pass and no give-up counter.
 */
export const extractGoal = async (
  side: Pick<SideRunner, "classify">,
  taskPrompt: string,
  log: Logger,
): Promise<string | null> => {
  try {
    const result = await side.classify({
      system: GOAL_EXTRACTION_SYSTEM,
      user: taskPrompt,
      schema: ExtractedGoalSchema,
    });
    const goal = result.goal.trim();
    return goal.length >= MIN_GOAL_LENGTH ? goal : null;
  } catch (error) {
    log.warn({ err: error }, "goal extraction failed — proceeding on task-prompt basis");
    return null;
  }
};

/** Execute one background instance through the iterative evaluator loop. */
export const executeBackgroundInstance = async (
  deps: ExecutorDeps,
  instance: TaskInstanceRecord,
  signal?: AbortSignal,
): Promise<void> => {
  const { repository, side, emit, runPostProcessors, maxIterations, timezone, now, log } = deps;

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
    emit(NOTIFY_EVENT, {
      text: `❌ ${noticeMessage}`,
      severity: SEVERITIES.warning,
      source,
    });
    log.warn({ instanceId: instance.id, reason }, "background task failed");
  };

  // A resumable instance is a waiting run whose user reply has arrived: pick up
  // from the captured progress rather than restarting the task from scratch.
  const resuming = instance.status === "waiting" && instance.userResponse != null;

  repository.updateInstance(instance.id, {
    status: "running",
    startedAt: instance.startedAt ?? now(),
  });
  log.info({ instanceId: instance.id, resuming }, "executing background task instance");

  // Legacy instances created before persistent background sessions carry no session file. A
  // resuming legacy instance replays its captured excerpt into a fresh session; everything else
  // resumes the persistent session and prompts only the new turn.
  const legacyResume = resuming && instance.piSessionFile == null && instance.question != null;

  let session: Awaited<ReturnType<BackgroundSide["openBackgroundSession"]>> | null = null;

  // Aborting the signal cancels a run: session.abort() ends a mid-flight prompt
  // gracefully, and signal.aborted is checked between iterations. On abort the
  // loop unwinds WITHOUT writing status — the cancel initiator owns that write,
  // so the executor never clobbers a `failed`-by-cancellation row.
  const onAbort = (): void => {
    session?.abort();
  };
  signal?.addEventListener("abort", onAbort);

  try {
    const system = buildSystemPrompt(now(), timezone);

    // Goal extraction at run start: a fresh (non-resuming) run with no snapshotted goal
    // derives one from the task prompt. The derived goal is stored on the instance for this
    // run and written back to the definition ONLY when its goal is still null (never clobbering
    // a goal set by update_task or an earlier write-back in the meantime). A resumed instance
    // already has its goal from its first start, so it never re-extracts. On failure or an
    // unusable result, the run proceeds on the task-prompt basis and persists nothing — every
    // later run retries until a usable goal lands (R3/R14/R15, DES-006 lazy migration).
    let effectiveGoal = instance.goal;
    if (!resuming && effectiveGoal == null) {
      const extracted = await extractGoal(side, instance.prompt, log);
      if (extracted != null) {
        repository.updateInstance(instance.id, { goal: extracted });
        effectiveGoal = extracted;
        if (definition != null) {
          repository.setDefinitionGoalIfNull(definition.id, extracted);
        }
      }
    }

    // Workspace context (memory, projects, …) is injected by each extension's background-scoped
    // context section via pi's before_agent_start hook — no manual fold into the opening prompt.
    // notify_user is the notifications extension's background-scoped tool (bound automatically);
    // the agent decides whether to use it per the task's own guidance.
    let pendingQuestion: string | null = null;

    const askUserTool = defineTool({
      name: "ask_user",
      label: "Ask User",
      description:
        "Ask the user a blocking question and pause the task until they reply. Use ONLY when you genuinely cannot proceed without their input — not for questions you can answer from available context. After calling this, stop working: the task suspends and resumes automatically once the user responds.",
      parameters: Type.Object({
        question: Type.String({ description: "The question to put to the user" }),
      }),
      execute: async (_id, params) => {
        pendingQuestion = params.question;

        return {
          content: [
            {
              type: "text",
              text: "Question delivered to the user. The task is now paused — stop working and wait; it will resume automatically with their reply.",
            },
          ],
          details: {},
        };
      },
    });

    session = await side.openBackgroundSession({
      system,
      customTools: [askUserTool],
      // A legacy resume replays its excerpt into a fresh session; everything else resumes
      // the run's own persistent session file when one exists.
      sessionFile: legacyResume ? null : instance.piSessionFile,
    });

    // Record the session file immediately so a mid-run crash still leaves the resumable path.
    const transcriptPath = session.sessionFile ?? null;
    if (transcriptPath !== instance.piSessionFile) {
      repository.updateInstance(instance.id, { piSessionFile: transcriptPath });
    }

    // First turn: the resumed persistent session already holds its history, so it gets only the
    // user's reply; a legacy resume replays the captured excerpt; a fresh run gets task + goal.
    let prompt =
      resuming && !legacyResume
        ? (instance.userResponse as string)
        : legacyResume && instance.question != null
          ? buildResumePrompt(
              instance.prompt,
              instance.resumeContext ?? "",
              instance.question,
              instance.userResponse as string,
            )
          : buildOpeningPrompt(instance.prompt, effectiveGoal);

    for (let iteration = 1; iteration <= maxIterations; iteration += 1) {
      // Covers a cancel that landed before this iteration (between turns, or
      // before the first prompt ever runs).
      if (signal?.aborted) {
        log.info(
          { instanceId: instance.id, iteration },
          "background task aborted between iterations",
        );
        break;
      }

      await session.prompt(prompt);

      // Covers a cancel that interrupted the prompt itself (session.abort()
      // resolved it mid-flight). Bail before persisting any waiting transition.
      if (signal?.aborted) {
        log.info(
          { instanceId: instance.id, iteration },
          "background task aborted between iterations",
        );
        break;
      }

      const text = lastAssistantText(session.messages);

      if (pendingQuestion != null) {
        repository.updateInstance(instance.id, {
          status: "waiting",
          question: pendingQuestion,
          resumeContext: text,
          userResponse: null,
          piSessionFile: transcriptPath,
        });

        emit(NOTIFY_EVENT, {
          text: `❓ Needs your input (instance ${instance.id}):\n\n${pendingQuestion}`,
          severity: SEVERITIES.urgent,
          source,
        });

        log.info({ instanceId: instance.id }, "background task paused awaiting user input");
        return;
      }

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
          question: null,
          resumeContext: null,
        });
        // Dispose before post-processing so memory extraction reads a flushed transcript; the
        // session's pi JSONL feeds episodic/topics extraction (transcriptPath is now non-null).
        session.dispose();
        session = null;
        await runPostProcessors({ trunk: null, transcriptPath, log });
        // No programmatic success notice: the agent surfaces results via notify_user at
        // its own discretion (per the task prompt).
        log.info({ instanceId: instance.id }, "background task completed");
        return;
      }

      if (evaluation.status === "error") {
        fail(`Agent stuck: ${evaluation.reason}`, `Task failed: ${evaluation.reason}`);
        return;
      }

      prompt = buildContinuationPrompt(evaluation.reason);
    }

    // Skipped on cancel: the loop `break`s out of a cancellation, and the cancel
    // initiator has already written the terminal `failed` row.
    if (!signal?.aborted) {
      fail(
        `Max iterations (${maxIterations}) reached without completion`,
        `Task failed: reached max iterations (${maxIterations})`,
      );
    }
  } catch (error) {
    // A cancellation may surface here if abort raced with an in-flight operation;
    // the initiator owns the terminal write, so log and swallow rather than fail.
    if (signal?.aborted) {
      log.info({ instanceId: instance.id }, "background task cancelled");
    } else {
      log.error({ instanceId: instance.id, err: error }, "background task errored");
      fail(`Task failed: ${error}`, `Task failed with error: ${error}`);
    }
  } finally {
    // Guarantees the live session handle never leaks — the complete path nulls it after disposing.
    signal?.removeEventListener("abort", onAbort);
    session?.dispose();
  }
};

/**
 * Tick-driven dispatcher for background instances. In-flight executions are
 * tracked across ticks so a slow run is never dispatched twice, and a hard
 * concurrency cap bounds how many run at once — surplus pending instances are
 * left untouched and picked up on a later tick as slots free.
 */
export class BackgroundRunner {
  private readonly deps: ExecutorDeps;
  // One entry per dispatched instance, pairing its in-flight run with the
  // AbortController that cancels it. Binding them in a single map (rather than
  // two kept in lockstep) makes the controller/promise pairing structural.
  private readonly runs = new Map<
    string,
    { controller: AbortController; promise: Promise<void> }
  >();

  constructor(deps: ExecutorDeps) {
    this.deps = deps;
  }

  tick(): void {
    const dispatchable = [
      ...this.deps.repository.getResumableInstances("background"),
      ...this.deps.repository.getPendingInstances("background"),
    ];

    for (const instance of dispatchable) {
      if (this.runs.size >= this.deps.maxConcurrent) {
        this.deps.log.debug(
          {
            instanceId: instance.id,
            active: this.runs.size,
            maxConcurrent: this.deps.maxConcurrent,
          },
          "background dispatch deferred — concurrency cap reached",
        );
        break;
      }

      if (this.runs.has(instance.id)) continue;

      const controller = new AbortController();
      const promise = executeBackgroundInstance(this.deps, instance, controller.signal)
        .catch((error) =>
          this.deps.log.error(
            { instanceId: instance.id, err: error },
            "background executor crashed",
          ),
        )
        .finally(() => this.runs.delete(instance.id));

      this.runs.set(instance.id, { controller, promise });
    }
  }

  /**
   * Cancel an in-flight instance: signal its evaluator loop to abort and await
   * the run so the caller sees it settled. Returns false when the instance is
   * not running in this process (the caller's own terminal write is then the
   * whole effect).
   */
  async cancel(instanceId: string): Promise<boolean> {
    const run = this.runs.get(instanceId);
    if (run == null) return false;

    run.controller.abort();
    // `run.promise` already swallows its own errors via the `.catch` in `tick`,
    // so awaiting it never throws — it just waits for the run to unwind.
    await run.promise;
    return true;
  }

  /** Await in-flight executions (tests and shutdown). */
  async drain(): Promise<void> {
    await Promise.allSettled([...this.runs.values()].map((r) => r.promise));
  }
}

import type { Logger } from "../../log.ts";
import type { AgentApi } from "../api.ts";

/**
 * Shadow-fork conversation-boundary classifier. On each idle user message it forks the live branch
 * into a throwaway headless session and asks the conversation itself how the message should be handled:
 * continue the current topic, shift to a new one, or (DLT-181) park/return a short, unrelated side task against a checkpoint.
 * The live session is never mutated (R6). Detection fails open (R11): any error or unparseable output is
 * treated as "continue" so a classifier failure never blocks the message.
 */

export interface ShiftDeps {
  shadowFork: AgentApi["shadowFork"];
  /** The live system prompt to inherit in the shadow, so it classifies as the same assistant. */
  getSystemPrompt: () => string;
  log: Logger;
}

export interface ShiftInput {
  sessionFile: string;
  /**
   * Whether the current branch has at least one completed assistant turn since its base. The
   * empty-branch guard skips classification (and the fork) for a branch too small to summarize, so
   * two shifts in immediate succession cannot collapse an empty branch.
   */
  currentBranchHasAssistantTurn: boolean;
  message: string;
  /**
   * Whether a checkpoint is currently active (a side task is in flight). The classifier runs on a
   * detached shadowFork and cannot read the live trunk's state, so the middleware injects this. It
   * gates which checkpoint decision is even valid: `set-checkpoint` only when none is active,
   * `summarize-to-checkpoint` only when one is (S3/KD8).
   */
  checkpointActive: boolean;
}

/**
 * The classifier's decision. `continue`/`shift` are the original topic-boundary results; the two
 * checkpoint results (DLT-181) drive the side-task auto-flow: `set-checkpoint` parks the main line
 * when a self-contained, unrelated side task begins (e.g. an expense logged mid-workflow, a quick
 * note/reminder capture), and `summarize-to-checkpoint` folds that side task back when the
 * conversation returns to the main line — explicitly (a "going back" reference or a clear topic
 * match), or implicitly when the side task is done and the next message does not continue it but
 * fits the main line (issue-419).
 */
export type ShiftDecision = "continue" | "shift" | "set-checkpoint" | "summarize-to-checkpoint";

/**
 * Build the classifier prompt. The checkpoint section is conditional on `checkpointActive` so only the
 * valid checkpoint decision for the current state is offered (the middleware gates defensively too).
 * The prompt first asks the model to judge whether the current topic is still open or has reached a
 * natural finish (issue-423): a finished topic has no main line to park and resume, so an unrelated
 * message shifts rather than checkpointing, and `set-checkpoint` is gated on an open topic. The model
 * infers finish-state from the conversation history the shadow fork already reads, so — unlike
 * `checkpointActive` — no signal is injected. Posture is deliberately conservative (KD8): a checkpoint
 * decision is emitted only when it is clearly the right call; ambiguity falls back to `continue`
 * (or `shift` for a genuinely new topic).
 */
const buildClassifierPrompt = (message: string, checkpointActive: boolean): string =>
  [
    "I am deciding how this conversation should proceed with a new user message.",
    "Answer from the perspective of this current conversation and choose exactly one decision.",
    "",
    "First, judge whether the current topic is still OPEN or has reached a natural FINISH. It is OPEN",
    "when there is active work in progress, an unresolved question, or a task only partway through. It",
    "has reached a natural FINISH when the last exchange wrapped the topic up — the work was completed,",
    "a question was fully answered, the user acknowledged or accepted the result, and no open thread is",
    "left to continue.",
    "",
    "Decisions:",
    '- "continue": the message is a follow-up, clarification, answer to me, correction, short reply,',
    "  reaction, or otherwise continues the current thread — even if the last exchange looked wrapped",
    "  up, a genuine follow-up reopens the topic. This is the default when uncertain.",
    '- "shift": the message starts a new topic that should become the main line and begin fresh.',
    "  Choose this in either of two cases: (a) the current topic has reached a natural FINISH and the",
    "  message is about something unrelated to it — regardless of how small or simple the new topic is,",
    "  because a finished topic leaves no main line to park; or (b) the current topic is still OPEN but",
    "  the message is a substantive new task or question that should become the main line, setting the",
    "  open topic aside.",
    ...(checkpointActive
      ? [
          '- "summarize-to-checkpoint": a checkpoint is currently active (a side task is in flight). The',
          "  main line is the conversation that came before this side task. Choose this when the message",
          "  returns to that main line — either it explicitly references going back to or resuming what was",
          '  discussed before the side task (e.g. "going back to the report"), or its topic',
          "  clearly matches the main-line topic. It is ALSO a return — and the most common case — when the",
          "  message does NOT follow on from the side task's last turn (the side task looks done or set",
          "  aside) and would read naturally as the next step of the main-line conversation, even without",
          '  naming it; in that case lean toward "summarize-to-checkpoint" rather than leaving it on',
          '  "continue". This is a RETURN to an existing conversation, not a new topic, so do not choose',
          '  "shift" for it. Keep choosing "continue" for further side-task turns, and choose "shift" only',
          "  for a genuinely new topic that is neither the side task nor the main line. When genuinely",
          '  unsure whether the message returns to the main line, prefer "continue".',
        ]
      : [
          '- "set-checkpoint": the message begins a self-contained side task that is distinct from and',
          "  unrelated to the current conversation — the user is interleaving something separate while the",
          "  main thread stays parked and resumes once the side task is done. Typical cases: capturing a",
          "  note or reminder, logging something (an expense, a transaction), a quick lookup or brief",
          "  self-contained question, or a background task or daily ceremony starting its own short",
          "  exchange. Choose this when the message clearly starts a separate task rather than following on",
          "  from the current one, regardless of how many turns the side task may take — but ONLY when the",
          "  current topic is still OPEN, so there is an active main line to park and resume. A checkpoint",
          "  parks an active main line to return to, so it never applies once the topic has reached a",
          '  natural FINISH: then choose "shift" instead, even for a small capture like logging an expense',
          '  or a quick reminder. Prefer "continue" for follow-ups, clarifications, or replies to the',
          '  current task. When unsure whether the message is a separate side task, prefer "continue".',
        ]),
    "",
    "Be conservative about parking a side conversation: only choose a checkpoint decision when it is",
    'clearly the right call; when unsure, prefer "continue" (or "shift" for a clearly new topic).',
    'Return exactly one JSON object and no other text: {"decision":"continue"|"shift"|"set-checkpoint"|"summarize-to-checkpoint","reason":"short reason"}.',
    "",
    "<candidate_user_message>",
    message,
    "</candidate_user_message>",
  ].join("\n");

const isDecision = (value: unknown): value is ShiftDecision =>
  value === "continue" ||
  value === "shift" ||
  value === "set-checkpoint" ||
  value === "summarize-to-checkpoint";

/**
 * Tolerant JSON parse: extract the first balanced object and read `decision`. Anything that is not a
 * recognized decision value — including unparseable output — degrades to "continue" (fail-open, R11).
 */
const parseDecision = (text: string, log: Logger): ShiftDecision => {
  const match = text.match(/\{[\s\S]*\}/);

  if (match == null) return "continue";

  try {
    const decision = (JSON.parse(match[0]) as { decision?: unknown }).decision;
    return isDecision(decision) ? decision : "continue";
  } catch (error) {
    log.debug(
      { err: error },
      "topic-shift classifier output unparseable — continuing current topic",
    );
    return "continue";
  }
};

export const classifyShift = async (deps: ShiftDeps, input: ShiftInput): Promise<ShiftDecision> => {
  if (!input.currentBranchHasAssistantTurn) {
    return "continue";
  }

  let fork: Awaited<ReturnType<AgentApi["shadowFork"]>> | null = null;

  try {
    fork = await deps.shadowFork(input.sessionFile, {
      systemPrompt: deps.getSystemPrompt(),
      tier: "classifier",
    });

    return parseDecision(
      await fork.prompt(buildClassifierPrompt(input.message, input.checkpointActive)),
      deps.log,
    );
  } catch (error) {
    deps.log.error({ err: error }, "topic-shift classification failed — continuing current topic");
    return "continue";
  } finally {
    if (fork != null) {
      await fork.dispose().catch((error) => {
        deps.log.warn({ err: error }, "shadow-fork dispose failed");
      });
    }
  }
};

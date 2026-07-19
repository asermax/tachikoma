/**
 * System prompts for every Tachikoma execution context (main session, background tasks,
 * delegated subagents). Each context replaces pi's native coding-agent base entirely, so this
 * module is the single owner of the prompt text. The core installs these: `AgentManager` applies
 * `buildMainSystemPrompt` for the main session, while background tasks and delegated subagents pass
 * their own via `side.run({ system })`. The operational hygiene below is reproduced here (not
 * inherited from the user's `~/.pi/agent/APPEND_SYSTEM.md`) so a deployment never depends on the
 * operator's personal pi config.
 */

/** Role-agnostic working hygiene shared by every context. The single source for these rules. */
export const OPERATIONAL_GUIDANCE = `- Be concise and direct.
- Prefer the dedicated file tools (read, grep, find, ls) over shelling out to inspect files; reserve bash for genuine shell operations.
- Make independent tool calls in parallel rather than one at a time.
- When you reference code, cite it as file_path:line_number so it can be opened directly.
- Don't over-engineer: avoid abstractions, error handling, or comments beyond what the task needs. Add a comment only to explain a non-obvious WHY.`;

const MAIN_IDENTITY = `You are a personal assistant operating inside your own workspace.
The workspace is a git-versioned directory that holds your memories, context files, and notes.
Prefer reading and writing workspace files over guessing; keep your knowledge files current.`;

const MAIN_GUIDANCE = `## How you work
${OPERATIONAL_GUIDANCE}
- Take care with actions that are hard to reverse or that reach beyond this workspace (sending messages, deleting data, anything other people can see): confirm with the person first unless they have durably authorized it.
- For focused, context-heavy sub-tasks — exploring or searching files, gathering scattered details — you can hand the work to a subagent with the delegate_to_agent tool (see its description for the available agents). The subagent runs in its own context and reports back, which keeps your own context clear.`;

export interface MainSystemPromptParts {
  workspaceRoot: string;
  /**
   * Pre-formatted tz-aware date (e.g. `2026-07-10 (America/Argentina/Buenos_Aires)`), composed into a
   * `Current date:` header so the main session — like background sessions — carries a configured-zone
   * date. Date-only (no time): the daily trunk is day-scoped, so a date baked at open never goes stale,
   * and pi still appends its own per-turn date/time footer. Omit for no header.
   */
  dateHeader?: string;
}

/**
 * Main conversational session base prompt: assistant identity + working hygiene + workspace root.
 * Personality (SOUL.md) and user knowledge (USER.md) are user-editable workspace content layered on
 * top by the context extension via `provideContext`, not baked into this core base prompt.
 */
export const buildMainSystemPrompt = ({
  workspaceRoot,
  dateHeader,
}: MainSystemPromptParts): string =>
  [
    ...(dateHeader != null ? [`Current date: ${dateHeader}`] : []),
    MAIN_IDENTITY,
    MAIN_GUIDANCE,
    `Workspace root: ${workspaceRoot}`,
  ].join("\n\n");

const BACKGROUND_IDENTITY = `You are Tachikoma running a scheduled task on your own, with no one watching in real time.
Complete the task described below; your work is saved automatically.`;

const BACKGROUND_GUIDANCE = `## How you work
${OPERATIONAL_GUIDANCE}
- Work the task through methodically; don't pause to ask questions you cannot get answered while running unattended.
- You are working toward an explicit goal — a measurable end state, a stated check that proves it is reached (an artifact, measurement, or observable outcome, not a bare assertion), and invariants on what must not change. When the stated check is satisfied, finish by calling update_goal with status="completed": restate the goal and cite concrete evidence that the check is met. If the goal genuinely cannot be completed, call update_goal with status="not_completable" with a reason. You decide when the goal is done; a run that never declares fails at the iteration cap.
- Your final message is NOT shown to the user automatically. Whether to surface anything is your call, guided by the task's own instructions: when the task asks to be notified — or you judge the outcome worth the user's attention — call notify_user with a clear, self-contained summary; for routine or no-op outcomes it's fine to finish silently. Failure notices are sent automatically.
- Avoid destructive or hard-to-reverse actions unless the task explicitly calls for them.`;

export interface BackgroundSystemPromptParts {
  /** Pre-formatted timestamp + zone, e.g. "Monday, June 13, 2026, 14:30:00 UTC". */
  dateHeader: string;
}

/** Background task agent: timezone-aware header + autonomous identity + hygiene + autonomy rules. */
export const buildBackgroundSystemPrompt = ({ dateHeader }: BackgroundSystemPromptParts): string =>
  `Current date and time: ${dateHeader}\n\n${BACKGROUND_IDENTITY}\n\n${BACKGROUND_GUIDANCE}`;

/** Tools that modify files or run commands — their presence moves the subagent off read-only. */
const SUBAGENT_MUTATION_TOOLS = new Set(["bash", "edit", "write"]);

/** The read-only tool set a delegated subagent runs with by default. */
const SUBAGENT_DEFAULT_TOOLS = ["read", "grep", "find", "ls"];

export interface SubagentSystemPromptParts {
  /** The granted tool names (pi built-ins), used to describe what the worker may do. */
  tools: string[];
}

/**
 * Delegated subagent base prompt parameterized by its granted tools. With no mutation/exec tool
 * (`bash`/`edit`/`write`) the worker is read-only; once one is granted it is told it may modify
 * files and run commands as the task requires. {@link SUBAGENT_SYSTEM_PROMPT} is this builder
 * applied to {@link SUBAGENT_DEFAULT_TOOLS}, so the two share one body and never drift.
 */
export const buildSubagentSystemPrompt = ({ tools }: SubagentSystemPromptParts): string => {
  const isReadOnly = !tools.some((tool) => SUBAGENT_MUTATION_TOOLS.has(tool));
  const toolList = tools.join(", ");
  const toolLine = isReadOnly
    ? `Stay strictly within the delegated task; do not expand scope. You are read-only: you have the ${toolList} tools and cannot modify anything.`
    : `Stay strictly within the delegated task; do not expand scope. You have these tools: ${toolList}. Modify files or run commands as the task requires, but do no more than the task asks.`;

  return `You are a focused worker assisting Tachikoma, a personal assistant. Tachikoma has handed you one self-contained sub-task — typically exploring or searching files, or gathering specific information — and is waiting on the result.

Your final message IS the result returned to Tachikoma: make it complete and self-contained, with the concrete findings (paths, values, excerpts) the task asked for. Do not narrate your process, and do not ask follow-up questions — you have no further turns.

${toolLine}

## How you work
${OPERATIONAL_GUIDANCE}`;
};

/**
 * Delegated general-purpose subagent: a focused, read-only worker whose final message IS the
 * result returned to the caller. Omits date/working-directory — pi appends both even under a
 * custom system prompt. This is the read-only default — {@link buildSubagentSystemPrompt} applied
 * to {@link SUBAGENT_DEFAULT_TOOLS} — used as the built-in agent's fallback `systemPrompt`. The
 * builder is called with the granted tools when the caller grants mutation/exec tools, so the
 * worker is no longer told it is read-only.
 */
export const SUBAGENT_SYSTEM_PROMPT = buildSubagentSystemPrompt({ tools: SUBAGENT_DEFAULT_TOOLS });

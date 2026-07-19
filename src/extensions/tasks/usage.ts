/**
 * Usage guidance injected into the agent's context so it knows how — and when — to use the
 * task scheduling subsystem, beyond the bare tool descriptions. Scoped to main + background.
 */
export const buildTasksUsage = (timezone: string | undefined): string => {
  const zone = timezone ?? "the system timezone";

  return `## Tasks

You can schedule work to happen proactively — reminders, periodic checks, follow-ups, and autonomous jobs — instead of waiting to be asked each time.

Two task types:
- **session** — the prompt is injected into the conversation when the user is next idle. Use it when you need the user to see or respond (reminders, check-ins, questions).
- **background** — runs autonomously in its own isolated session, no user present. Use it for self-contained work (gathering, processing, analysis, maintenance). A background run can \`notify_user\` with findings and \`ask_user\` a blocking question; failures notify automatically.

Scheduling (evaluated in ${zone}):
- **cron** for recurring tasks (\`0 9 * * *\` daily 9am, \`0 */2 * * *\` every 2h).
- **ISO datetime** for one-shots (\`2026-04-01T15:00:00\` = local; add \`Z\`/offset for explicit zone). One-shots auto-disable after firing.

### Background task goals

A background task works toward an explicit **goal** — what "done" means for the run, in the agent's own terms. A good goal has three parts:
- an **end state**: the measurable, concrete outcome the task produces;
- a **stated check**: how the agent proves that end state is reached — an artifact, measurement, or other observable outcome (not a bare assertion);
- **invariants**: constraints on what must NOT change while getting there (for example, "don't break existing behavior" or "don't modify files outside the project").

\`goal\` is optional on \`create_task\`, \`update_task\`, and \`run_task_now\`. If you omit it, the first background run derives one from the prompt and saves it for later runs. The run then decides for itself when the goal is met: it finishes by calling \`update_goal\` with \`status="completed"\` (restating the goal and citing concrete evidence that the stated check is satisfied) or \`status="not_completable"\` with a reason that reaches the user. A run that never declares fails at the iteration cap.`;
};

import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance injected into the agent's context so it knows how — and when — to use the
 * task scheduling subsystem, beyond the bare tool descriptions. Scoped to main + background.
 * The goal block stays on this surface per R13 (goal meaning documented on every
 * agent-facing surface); the deeper run mechanics live in the reference file, and the
 * background session's own base prompt (`BACKGROUND_GUIDANCE`) restates them for the run.
 */
export const buildTasksUsage = (timezone: string | undefined): string => {
  const zone = timezone ?? "the system timezone";

  return `## Tasks

You can schedule work to happen proactively — reminders, periodic checks, follow-ups, autonomous jobs — instead of waiting to be asked.

Two task types:
- **session** — the prompt enters the conversation when the person is next idle; use it when they should see or respond (reminders, check-ins, questions).
- **background** — runs autonomously in its own isolated session; use it for self-contained work (gathering, processing, analysis, maintenance). It can notify the person or ask a blocking question; failures notify automatically.

Scheduling (evaluated in ${zone}): **cron** for recurring work (\`0 9 * * *\` daily 9am, \`0 */2 * * *\` every 2h), or an **ISO datetime** for a one-shot (\`2026-04-01T15:00:00\` local; add \`Z\`/offset for explicit zone) — one-shots auto-disable after firing.

### Background task goals

Give a background task an explicit **goal** — what "done" means for the run: an **end state** (the measurable outcome), a **stated check** proving it reached it (an artifact, measurement, or other observable outcome with evidence — not a bare assertion), and **invariants** on what must NOT change getting there.

\`goal\` is optional on \`create_task\`/\`update_task\`/\`run_task_now\`; omit it and the first run derives one from the prompt. The run declares its own outcome: \`update_goal\` \`status="completed"\` (restating the goal, citing evidence) or \`status="not_completable"\` with a reason that reaches the person.

${referencePointer(import.meta.dirname, "tasks")}`;
};

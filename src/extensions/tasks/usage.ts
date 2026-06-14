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

Tools: \`create_task\`, \`list_tasks\`, \`get_task\`, \`update_task\`, \`delete_task\` manage definitions; \`run_task_now\` fires a background task immediately (an existing one by id/name, or an ad-hoc prompt); \`query_task_instances\` inspects past/current firings. Prefer \`list_tasks\`/\`get_task\` before creating to avoid duplicates; disable with \`update_task enabled=false\` rather than deleting when you may want it back. In a conversation, \`respond_to_task\` forwards the user's reply to a background task that is waiting for input.`;
};

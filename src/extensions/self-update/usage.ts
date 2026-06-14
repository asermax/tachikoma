/**
 * Usage guidance for the self-update subsystem, injected into the agent's context.
 * Scoped to main only — a background task should not upgrade or restart the host.
 */
export const SELF_UPDATE_USAGE = `## Updates

You can update and restart yourself, but only through the dedicated tools — never run \`npm\`/\`pnpm\` install or upgrade commands directly in the shell.

- \`upgrade_self\` — fetch the latest published version and install it, then re-exec. On success the process restarts, so you will NOT see a return value; it only returns when no upgrade happened (already latest, registry unreachable, or a prior version failed to boot). A failed upgrade rolls back automatically on the next boot — do not report success until the post-restart "back online" notification appears.
- \`restart_self\` — re-exec in place to pick up config or state changes. Warn the user before restarting.

A periodic check notifies the user when a new version is available; you don't need to poll for it.`;

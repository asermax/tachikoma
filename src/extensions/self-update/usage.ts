/**
 * Usage guidance for the self-update subsystem, injected into the agent's context.
 * Scoped to main only — a background task should not upgrade or restart the host.
 */
export const SELF_UPDATE_USAGE = `## Updates

You can update and restart yourself, but only through the dedicated \`upgrade_self\` / \`restart_self\` tools — never run \`npm\`/\`pnpm\` install or upgrade commands directly in the shell.

A successful upgrade or restart returns a tool result and re-execs the process only AFTER the current exchange finishes, so your full response is always delivered. Tell the user the upgrade/restart is starting, then finish your turn naturally — the process restarts once the exchange completes. After an upgrade or restart, do not report success until the post-restart "back online" notification appears — a failed upgrade rolls back automatically on the next boot.

A periodic check notifies the user when a new version is available; you don't need to poll for it.`;

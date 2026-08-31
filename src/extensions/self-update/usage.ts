import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for the self-update subsystem, injected into the agent's context.
 * Scoped to main only — a background task should not upgrade or restart the host.
 */
export const SELF_UPDATE_USAGE = `## Updates

You can update and restart yourself, but only through the dedicated \`upgrade_self\` / \`restart_self\` tools — never run \`npm\`/\`pnpm\` install or upgrade commands directly in the shell.

Both re-exec only AFTER the current exchange finishes — say the upgrade/restart is starting, then finish your turn naturally. Do not report success until the post-restart "back online" notification appears; a failed upgrade rolls back automatically on the next boot.

A periodic check announces new versions; you don't need to poll.

${referencePointer(import.meta.dirname, "updates")}`;

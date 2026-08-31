import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for the detached-process subsystem, injected into the agent's context.
 * Scoped to main + background. The no-bash-backgrounding rule and check-before-judging stay
 * inline; output-capture and limit details live in the reference file.
 */
export const DETACHED_PROCESSES_USAGE = `## Detached Processes

Long-running OS shell commands can outlive your turn and Tachikoma itself via the detached process tools. Do NOT background a command with bash (\`&\`, \`nohup\`): it dies with the turn.

Check on a runner with \`query_process\`/\`read_process_output\` rather than assuming it finished, and read its output before judging it stalled or failed. You are notified when a process exits.

${referencePointer(import.meta.dirname, "processes")}`;

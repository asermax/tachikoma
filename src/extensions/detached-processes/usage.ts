/**
 * Usage guidance for the detached-process subsystem, injected into the agent's context.
 * Scoped to main + background.
 */
export const DETACHED_PROCESSES_USAGE = `## Detached Processes

You can start and monitor long-running OS shell commands that outlive a single turn — and Tachikoma itself. Reach for these when you need a worker, a server, a build, or any command that keeps running while you do other things; do NOT background commands with the bash tool (\`&\`, \`nohup\`, etc.) — they die with the turn.

Processes are memory-limited (per-process or a default) and you are notified when one exits. Check on a long-runner with \`query_process\`/\`read_process_output\` rather than assuming it finished.`;

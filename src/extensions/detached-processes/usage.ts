/**
 * Usage guidance for the detached-process subsystem, injected into the agent's context.
 * Scoped to main + background.
 */
export const DETACHED_PROCESSES_USAGE = `## Detached Processes

You can start and monitor long-running OS shell commands that outlive a single turn — and Tachikoma itself. Reach for these when you need a worker, a server, a build, or any command that keeps running while you do other things; do NOT background commands with the bash tool (\`&\`, \`nohup\`, etc.) — they die with the turn.

Tools:
- \`dispatch_detached_process\` — start a command (\`name\`, \`command\`; optional \`cwd\`, \`env\`, \`memory_limit_mb\`). Output is captured to log files; the process survives restarts.
- \`query_process\` — running processes by default, \`archived=true\` for exited ones, or a single \`process_id\` for full detail (status, PID, exit code, memory, OOM).
- \`read_process_output\` — read a process's stdout/stderr log (tails by default; page with \`offset\`/\`count\`).
- \`terminate_process\` — stop a process (SIGTERM, escalating to SIGKILL after \`grace_seconds\`).
- \`rename_process\` — relabel a record; \`delete_process\` — drop an exited record (refuses while running).

Processes are memory-limited (per-process or a default) and you are notified when one exits. Check on a long-runner with \`query_process\`/\`read_process_output\` rather than assuming it finished.`;

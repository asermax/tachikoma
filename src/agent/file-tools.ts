/**
 * Built-in pi tool names allowed when a session is forked for silent file maintenance
 * (memory extraction, core-context updates, nightly maintenance ticks). Passed to
 * `forkAndContinue`/`run` to hard-limit the fork to reading and editing files — it cannot
 * message the user, fire tasks, or reach any other tool.
 */
export const FILE_EDIT_TOOLS = ["read", "grep", "find", "ls", "edit", "write"];

/**
 * The read-only subset of pi's built-in tools: reading and locating files with no mutation
 * or execution (no `edit`/`write`/`bash`). The read-only counterpart to {@link FILE_EDIT_TOOLS},
 * which adds `edit`/`write`. Used by runs that must follow file paths a prompt references
 * without being able to alter the workspace (goal extraction, delegated read-only subagents).
 */
export const FILE_READ_TOOLS = ["read", "grep", "find", "ls"];

/**
 * Built-in pi tool names allowed when a session is forked for silent file maintenance
 * (memory extraction, core-context updates, nightly maintenance ticks). Passed to
 * `forkAndContinue`/`run` to hard-limit the fork to reading and editing files — it cannot
 * message the user, fire tasks, or reach any other tool.
 */
export const FILE_EDIT_TOOLS = ["read", "grep", "find", "ls", "edit", "write"];

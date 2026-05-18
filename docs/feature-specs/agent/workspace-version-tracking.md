# Workspace Version Tracking

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Automatic git version tracking for all workspace file changes. Every modification to memories, context files, and configuration is recorded with descriptive commits after each session, providing built-in history and rollback capability. A git post-processor runs in the pipeline's finalization phase — after all other processors complete — spawning a lightweight agent to group changes into cohesive commits. When an `origin` remote is configured, committed changes are pushed automatically.

## User Stories

- As a user, I want workspace changes automatically version-tracked so that I can review what changed and roll back if needed
- As the system, I need automatic git version tracking so that every workspace modification is recorded with descriptive commits
- As a user, I want committed workspace changes pushed to a remote so that my workspace history is backed up and accessible from other machines

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Automatic git version tracking for workspace changes via a post-processing pipeline step |
| R1 | Git post-processor registered in the pipeline's finalize phase, runs after all other processors complete |
| R2 | Post-processor spawns a Haiku agent via fresh `query()` (no session fork) to analyze and commit changes |
| R3 | Agent groups changes into cohesive commits by subdirectory/purpose |
| R4 | If no uncommitted changes, the post-processor completes as a no-op |
| R5 | Bootstrap hook initializes workspace as a git repo on first run (idempotent) |
| R6 | Commits use a fixed identity via repo-local git config (no global config dependency) |
| R7 | Linear history on a single branch — no branch operations |
| R8 | Bootstrap creates `.gitignore` with `.tachikoma/*.db` and `.tachikoma/logs/tachikoma.log` to exclude the DB binary and active log file from git tracking. Rotated log files (`.tachikoma/logs/tachikoma.<timestamp>.log`) are tracked by git. On every startup, missing gitignore entries are appended without committing (idempotent) |
| R9 | After committing, push committed changes to the `origin` remote with divergence detection and conflict resolution; on divergence, rebase local changes on top of remote before pushing |
| R10 | If no `origin` remote is configured, skip pushing silently (no-op) |
| R11 | On push failure (rebase failed, push failed after rebase), log a warning with the failure reason and continue; committed changes remain intact and will be retried on next sync |
| R12 | System prompt preamble instructs the assistant that workspace changes are pushed automatically when a remote is configured |
| R13 | `smart_pull` skips repositories with uncommitted changes to avoid data loss. `smart_push` runs `git rebase --autostash` so tracked dirty changes are transparently stashed and restored across the rebase (and across an agent-driven conflict resolution if one is needed). |
| R14 | `push` and `sync` MCP tools that wrap `smart_push` / `smart_pull` for the workspace (and registered project submodules); always available to the main coordinator agent and the task executor agent |
| R15 | PreToolUse deny hook on every non-git-processor agent surface that blocks destructive bash git commands: `git push`, `git reset`, `git checkout .`, `git restore .`, `git clean`, and mutating `git remote` subcommands |
| R16 | The deny hook splits compound commands (`&&`, `||`, `|`, `;`) and checks each sub-command independently |
| R17 | Read-only git commands (`status`, `log`, `diff`, `show`, `fetch`, `branch`, `remote -v`) and `git clone` pass through the deny hook unimpeded |
| R18 | After the commit agent completes and changes are pushed, the processor verifies the working tree is clean; if uncommitted changes remain, it retries once with a focused cleanup prompt before logging a warning |

## Behaviors

### Git Post-Processor (R1, R2, R3, R4, R9, R10, R11, R13, R18)

After all main-phase processors complete (memory extraction writes files), the git post-processor checks for uncommitted changes and spawns a Haiku agent to create cohesive commits.

**Acceptance Criteria**:
- Given uncommitted changes exist in the workspace, when the git post-processor runs, then it spawns a Haiku agent via a fresh `query()` call to analyze and commit workspace changes
- Given changes span multiple subdirectories, when the agent analyzes the diff, then it creates separate commits per cohesive group (e.g., episodic memories in one commit, facts in another)
- Given all changes belong to a single subdirectory, when the agent analyzes the diff, then it creates a single commit with a descriptive message
- Given no uncommitted changes exist, when the git post-processor runs, then it completes as a no-op without spawning an agent
- Given the agent completes, when the post-processor verifies the workspace, then it logs a warning if uncommitted changes remain
- Given uncommitted changes remain after the first commit pass, when the processor detects them, then it spawns a cleanup agent once with a focused retry prompt to commit remaining files
- Given the cleanup agent completes, when the post-processor verifies again, then it logs a warning if changes still remain (no further retries)
- Given the agent commits some groups but fails mid-way, then partial commits remain as valid history and uncommitted changes are picked up on the next run
- Given the projects post-processor has committed and pushed submodule changes in the pre_finalize phase, when the git post-processor runs in the finalize phase, then the resulting submodule reference changes appear in `git status` and are included in the workspace commits alongside other workspace changes
- Given an `origin` remote is configured and changes were committed, when the commit agent completes, then the post-processor pushes using divergence detection: fetches from origin, detects divergence, pushes directly if ahead, attempts naive rebase then agent-driven conflict resolution if diverged, and logs at info level on success
- Given no `origin` remote is configured, when the commit agent completes, then the post-processor skips pushing with a debug-level log only
- Given a push fails (e.g., rebase failed, push failed after rebase, auth error), when the push is attempted, then a warning is logged with the failure reason and the processor completes normally; committed changes remain intact and will be retried on next sync
- Given the remote has diverged and naive rebase fails, when divergence is detected, then a Haiku agent is spawned to resolve conflicts and complete the push
- Given naive rebase succeeds, when the rebase completes, then no merge commits exist and the history is linear (R7)
- Given conflict resolution succeeds, when the agent completes the rebase, then no merge commits exist and the history is linear (R7)
- Given a diverged remote and dirty tracked files during `smart_push`, when the rebase runs with `--autostash`, then git stashes the tracked changes, completes the rebase, restores the stash, and the push succeeds with REBASE_SUCCEEDED (R13)
- Given a diverged remote and an untracked file that would be overwritten by the rebase, when `git rebase --autostash` refuses to start, then `smart_push` returns REBASE_FAILED with no rebase state left behind and committed changes are preserved for the next attempt (R13)

### Commit Agent Behavior (R3, R7)

The Haiku agent inspects the workspace and creates well-organized commits using safe git commands only.

**Acceptance Criteria**:
- Given the agent is spawned, then for git it uses only `git status`, `git diff`, `git add`, and `git commit` — no destructive/history-rewriting commands. The commit agent is additionally allowed a curated set of read-only inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, `tail`, `wc`, `stat`) and navigation commands (`cd`, `pwd`) to help it understand workspace state before grouping commits; all other bash commands are denied by the `PreToolUse` gate hook. Compound commands (joined by `&&`, `||`, `|`, or `;`) are split and each sub-command is validated independently.
- Given the agent creates commits, then each commit message is descriptive and reflects the content of the group
- Given the agent creates commits, then it does not create or switch branches (linear history)
- Given the commit prompt, then it instructs the agent to verify the working tree is clean after committing and to go back and commit any files left behind

### Git Repo Initialization (R5, R6, R8, R14, R15, R16)

A bootstrap hook initializes the workspace as a git repo on first run, creates `.gitignore` for the DB binary, and syncs with the remote.

**Acceptance Criteria**:
- Given no `.git` directory in the workspace, when the git bootstrap hook runs, then a git repo is initialized with an initial empty commit
- Given a fresh init, when the hook completes, then repo-local `user.name` and `user.email` are configured with a fixed identity
- Given a fresh init, when the hook completes, then `.gitignore` contains `.tachikoma/*.db` and `.tachikoma/logs/tachikoma.log`, and the file is committed
- Given a fresh init with a pre-existing `.gitignore`, when the hook runs, then the DB binary and log patterns are appended without clobbering existing content
- Given an existing `.git` directory, when the hook runs, then it does not rewrite `.gitignore` (idempotent)
- Given an existing `.git` directory where `.gitignore` is missing the log entry, when the hook runs, then the missing entry is appended without committing (the commit agent picks it up)
- Given git init fails, when the hook runs, then a clear exception propagates with the failure reason

### Push/Sync MCP Tools (R14)

Two MCP tools — `push` and `sync` — expose the existing `smart_push` / `smart_pull` logic to the main coordinator agent and the task executor agent. Both tools target either the workspace or a registered project submodule.

**Acceptance Criteria**:
- Given the main agent has the `push` tool, when it calls `push(type="workspace")`, then `smart_push` is invoked against the workspace root with `origin`/`HEAD` and the tool response contains the resulting `PUSH_RESULT` value
- Given the main agent has the `push` tool, when it calls `push(type="project", target="my-app")` for a registered project, then `smart_push` runs against `<workspace>/projects/my-app` with `origin`/`HEAD` and the result enum is surfaced to the agent
- Given the main agent calls `push` or `sync` with `type="project"` and a `target` that is missing or does not resolve to a git repository under `projects/<target>`, when the tool runs, then it returns an `is_error` response naming the unknown target without invoking any git subprocess
- Given the main agent calls `sync(type=..., target=...)`, when the tool runs, then `smart_pull` runs first. If it returns `DIRTY_SKIPPED` or `SYNC_FAILED`, `smart_push` is not attempted and the pull result is surfaced. Otherwise, `smart_push` runs and both results are surfaced together

### Destructive Git Deny Hook (R15, R16, R17)

A PreToolUse hook installed on every non-git-processor agent surface (main coordinator agent, task executor agent) denies destructive bash git commands. Git-dedicated processor agents (GitProcessor and ProjectsProcessor commit agents, rebase resolver) are exempt — they keep their existing git allow-list unchanged.

**Acceptance Criteria**:
- Given the deny hook is installed, when the agent issues any form of `git push` (with or without flags) or `git reset`, then the bash call is denied with a clear reason and no subprocess runs
- Given the deny hook is installed, when the agent issues a compound command like `git status && git reset HEAD~1`, then the whole command is denied because one sub-command matches a destructive pattern
- Given the deny hook is installed, when the agent issues `git status`, `git log`, `git diff`, `git fetch`, `git remote -v`, `git clone <url> /tmp/foo`, or any non-git bash command, then the command passes through unchanged

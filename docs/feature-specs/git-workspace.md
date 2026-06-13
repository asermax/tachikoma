# Workspace Git Versioning

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Automatic git version tracking for the workspace. A bootstrap hook initializes the workspace as a git repo with a fixed committer identity and syncs it with its `origin` remote when one is configured. When a session closes, a finalize-phase post-processor stages everything, commits with a descriptive message generated from the staged diffstat, and pushes using divergence detection with rebase-based recovery (and an agent-assisted conflict-resolution step that falls back to aborting) instead of a bare push. Agent tools expose status, history, on-demand commits, and a destructive history scrub during conversations. A tool-call guardrail blocks the agent from running destructive git commands through its bash tool, steering it to the dedicated tools instead.

The sync primitives (`smartPull`/`smartPush`) are shared with the [projects](projects.md) extension, which applies the same semantics to each registered project repo.

## User Stories

- As a user, I want every workspace change (memories, context files, configuration) committed automatically after each session so that I can review history and roll back
- As a user, I want committed changes pushed to a remote so that my workspace is backed up and usable from other machines
- As the system, I want pushes to detect divergence and rebase instead of failing or force-pushing so that multiple machines can share one workspace remote safely
- As the system, when a rebase conflicts, I want a side agent to attempt to resolve it (within a bounded number of tries) and only abort if it cannot, so that genuinely conflicting machines self-heal without a human present instead of staying diverged
- As a user, I want to permanently purge a leaked secret or large blob from the entire workspace history so that it stops being recoverable from git
- As the system, I want the agent prevented from running destructive git (force push, reset, clean, history rewrite) through its bash tool so that workspace history is only mutated through the sanctioned, recoverable paths

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The workspace is a git repository; all changes are committed automatically when a session closes |
| R1 | The `init-workspace-repo` bootstrap hook initializes the repo on first run (idempotent): `git init`, fixed repo-local identity (`Tachikoma <tachikoma@local>`), commit signing disabled, an empty initial commit, and a committed `.gitignore` |
| R2 | Managed `.gitignore` entries (`.tachikoma/`) are appended on every startup when missing, without committing — the next commit pass picks them up |
| R3 | When an `origin` remote is configured, bootstrap syncs the workspace via `smartPull`; a dirty tree skips the sync with a warning; failures log and continue with local state |
| R4 | The `git-commit` post-processor (`finalize` phase) stages everything (`git add -A`) and commits with a one-line message generated from the staged diffstat via a processor-tier side completion; a clean tree is a no-op |
| R5 | Generated messages are sanitized (first non-empty line, surrounding quotes stripped, truncated at 100 characters); generation failure falls back to the deterministic `Update workspace files (YYYY-MM-DD)` |
| R6 | After committing, the processor pushes to `origin` via `smartPush` when the remote exists; push failures are logged and committed changes remain local |
| R7 | If uncommitted changes remain after the commit-and-push pass, the processor retries the commit once, then warns if changes still remain |
| R8 | `smartPush`: abort any stale in-progress rebase, fetch, classify divergence via merge-base ancestor checks; push directly when ahead; when diverged, attempt `rebase --autostash` then push; a conflicting rebase is handed to the agent resolver (R15) when one is wired, otherwise aborted; an unresolved conflict is aborted and surfaces as `REBASE_FAILED` with local commits preserved |
| R9 | `smartPull`: return `DIRTY_SKIPPED` without fetching when the tree is dirty; fast-forward when behind; rebase when diverged; a conflicting rebase is handed to the agent resolver (R15) when one is wired, otherwise aborted; an unresolved conflict is aborted and surfaces as `SYNC_FAILED` with local state restored |
| R10 | Sync helpers resolve `"HEAD"` to the actual local branch name and follow gitlink `.git` files, so divergence detection works in repos without an `origin/HEAD` ref and inside submodules |
| R11 | Agent tools `query_git_status`, `list_recent_commits`, `commit_workspace`, and `scrub` are registered in every agent session via a pi extension factory |
| R12 | The extension can be disabled entirely via `[extensions.git] enabled = false` |
| R13 | The `scrub` tool removes given paths from the entire workspace history via `git filter-repo --invert-paths` and force-pushes to `origin`; it requires a clean tree, requires the paths to exist in history, and degrades gracefully when `git filter-repo` is not installed. Outcomes are surfaced as a result enum, never thrown |
| R14 | A `tool_call` guardrail (registered via a second pi extension factory) blocks the agent's `bash` tool from running destructive git commands — `git push`, `git reset`, `git checkout/restore .`, `git clean`, mutating `git remote` subcommands, `git filter-repo`, and `git rebase` — and returns a message steering the agent to `commit_workspace`/`scrub`/automatic sync. Compound commands are split on shell operators (respecting quoting) and any matching sub-command blocks the whole command. Read-only git and `git clone` are not blocked |
| R15 | When `smartPush`/`smartPull` hit a rebase conflict and a `RebaseResolver` is wired (the git extension builds one from `app.agent.side`), the conflict is handed to a headless side agent that resolves the conflicted files, stages them, and continues the rebase via cwd-scoped `read_conflict`/`write_resolved`/`git` tools bound to the target repo; the `git` tool rejects push/fetch/reset/remote/filter-repo. The resolver runs in a bounded loop (at most 3 passes); completion is determined from the on-disk rebase state, not the agent's report. A resolved conflict continues the sync and surfaces as `AGENT_RESOLVED` (a member of `PUSH_SUCCESS`); an unresolved one falls back to abort (R8/R9). A thrown agent never aborts the sync |

## Behaviors

### Repo Initialization and Startup Sync (R1, R2, R3)

The bootstrap hook makes the workspace a usable repo before any extension writes to it, and pulls remote changes when an `origin` exists.

**Acceptance Criteria**:
- Given no `.git` directory, when the hook runs, then the repo is initialized with `user.name = Tachikoma`, signing disabled, an empty initial commit, and a second commit adding `.gitignore` with `.tachikoma/`
- Given the repo already exists, when the hook runs again, then HEAD and history are untouched (idempotent)
- Given an existing `.gitignore` missing the managed entry, when the hook runs, then `.tachikoma/` is appended without clobbering existing content and left uncommitted
- Given an `origin` remote with new commits, when the hook runs, then the workspace fast-forwards (or rebases) to the remote head
- Given no `origin` remote, when the hook runs, then the sync step is skipped silently; given a dirty tree, the sync is skipped with a warning; given a sync failure, startup continues on local state

### Session-Close Commit and Push (R0, R4, R5, R6, R7)

After post-processing has written its outputs (memory extraction and friends — see [memory.md](memory.md) and [conversation-loop.md](conversation-loop.md)), the `git-commit` processor records everything.

**Acceptance Criteria**:
- Given uncommitted changes exist, when the processor runs, then all changes are staged and committed in one commit whose message comes from a processor-tier completion over the staged diffstat
- Given the workspace is clean, when the processor runs, then no commit is made and no side completion is requested
- Given message generation fails, when committing, then the fallback `Update workspace files (YYYY-MM-DD)` is used
- Given an `origin` remote is configured, when the commit completes, then `smartPush` runs and a successful push (or rebase-then-push) is logged; a failed push logs a warning and the commits remain local
- Given files changed while the commit pass ran, when the processor verifies the tree, then it commits once more and warns if changes still remain afterwards

### Divergence Handling (R8, R9, R10, R15)

`smartPush` and `smartPull` (`src/extensions/git/sync.ts`) replace bare `git push`/`git pull` with explicit state machines returning result enums instead of throwing.

**Acceptance Criteria**:
- Given local and remote match, when pushing, then the result is `NOTHING_TO_PUSH`; when pulling, `UP_TO_DATE`
- Given local is ahead, when pushing, then a direct push runs and returns `PUSHED`
- Given local and remote diverged without conflicts, when pushing, then local commits are rebased onto the remote and pushed (`REBASE_SUCCEEDED`); when pulling, the rebase succeeds with the same linear result
- Given the divergence conflicts and a resolver is wired, when the resolver merges the conflict and completes the rebase, then the sync continues (push on `smartPush`) and the result is `AGENT_RESOLVED`
- Given the divergence conflicts and the resolver cannot resolve it within the bounded attempts (or no resolver is wired), then the rebase is aborted, the working tree ends clean, local commits are intact, and the result is `REBASE_FAILED` / `SYNC_FAILED`
- Given the local branch is behind, when pulling, then it fast-forwards (`FAST_FORWARDED`)
- Given the working tree is dirty, when pulling, then the result is `DIRTY_SKIPPED` and nothing is fetched
- Given a stale rebase was left by a crash, when either helper runs, then the rebase is aborted before any new operation

### Agent-Assisted Conflict Resolution (R15)

When a rebase conflicts, the sync helpers leave it in progress and delegate to a `RebaseResolver` — a headless side agent given cwd-scoped tools to read, rewrite, and `git add` the conflicted files and run `git rebase --continue`. The loop is bounded and completion is judged from disk state, so a misbehaving agent can neither spin forever nor falsely report success.

**Acceptance Criteria**:
- Given a conflicting rebase and a resolver that merges the files and finishes the rebase, when `smartPull` runs, then the result is `AGENT_RESOLVED` and the tree is clean with the remote commits present; when `smartPush` runs, then the resolved commits are pushed and the result is `AGENT_RESOLVED`
- Given a resolver that leaves the rebase still conflicted, when either helper runs, then the rebase is aborted, the tree ends clean with local commits intact, and the result is `SYNC_FAILED` / `REBASE_FAILED`
- Given a resolver that never finishes the rebase, when either helper runs, then the resolver is invoked at most 3 times before the helper gives up and aborts
- Given the resolver throws, when either helper runs, then the error is swallowed and the helper falls back to abort rather than propagating
- Given the resolver's `git` tool, when it is asked to run `push`, `fetch`, `reset`, `remote`, or `filter-repo`, then the command is refused; its file tools operate only on the repo being rebased
- Given no resolver is wired (the optional parameter is omitted), when a rebase conflicts, then the helper aborts exactly as before — `AGENT_RESOLVED` never occurs

### Agent Git Tools (R11)

Read-only inspection plus an explicit commit, for when the user wants changes saved before session end.

**Acceptance Criteria**:
- Given a clean tree, when `query_git_status` runs, then it reports the current branch and "Working tree is clean."; given pending changes, the truncated porcelain listing is returned
- Given a history, when `list_recent_commits` runs with a limit, then that many commits are listed newest-first as `hash date subject`; an empty repo yields "No commits found."
- Given pending changes, when `commit_workspace` runs with an explicit `message`, then it commits with that message and skips generation; without one, a message is generated from the staged diffstat
- Given a clean tree, when `commit_workspace` runs, then it reports there is nothing to commit

### History Scrub (R13)

`scrub` is the only sanctioned history rewrite — a deliberate, destructive purge of paths from the whole history, for leaked secrets or oversized blobs.

**Acceptance Criteria**:
- Given paths that exist in history and a clean tree, when `scrub` runs, then those paths are removed from every commit via `git filter-repo --invert-paths`; with an `origin` remote, the rewritten history is force-pushed and the (filter-repo-removed) remote is restored first
- Given a dirty working tree, when `scrub` runs, then it refuses with a clear message and does not rewrite anything
- Given a path absent from history, when `scrub` runs, then it reports the missing paths and does not rewrite anything
- Given `git filter-repo` is not installed, when `scrub` runs, then it returns a clear "not installed" message instead of failing opaquely
- Given the rewrite succeeds but the force-push fails, when `scrub` runs, then it reports the rewrite happened locally and the push must be retried; given no `origin` remote, the rewrite is reported as completed without a push

### Destructive-Git Guardrail (R14)

The guardrail keeps history mutation flowing only through the recoverable tools, so the agent cannot force-push, reset, or rewrite via raw bash.

**Acceptance Criteria**:
- Given the agent's `bash` tool is called with a destructive git command (`git push`, `git reset`, `git checkout/restore .`, `git clean`, mutating `git remote`, `git filter-repo`, `git rebase`), when the `tool_call` event fires, then the call is blocked with a reason naming the dedicated tools
- Given a non-destructive command (`git status`, `git log`, `git add`, `git commit`, `git fetch`, `git clone`, `ls`, …), when the `tool_call` event fires, then the call passes through unchanged
- Given a compound command where one sub-command is destructive, when it is evaluated, then the whole command is blocked; given a destructive keyword that appears only inside a quoted argument, then it is not blocked
- Given a non-bash tool call, when the `tool_call` event fires, then the guardrail ignores it

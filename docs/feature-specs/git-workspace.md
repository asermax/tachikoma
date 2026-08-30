# Workspace Git Versioning

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Automatic git version tracking for the workspace. A bootstrap hook initializes the workspace as a git repo with a fixed committer identity and syncs it with its `origin` remote when one is configured. When a session closes, a finalize-phase post-processor runs a headless agent that groups the changes into descriptive commits (falling back to a single deterministic commit if the agent fails), and pushes using divergence detection with rebase-based recovery (and an agent-assisted conflict-resolution step that falls back to aborting) instead of a bare push. Between exchanges, a debounced commit-push commits and pushes pending changes in the background after a configurable quiet window (default 5 minutes; `[scheduler] commitDebounceMinutes`, `0` to disable), so work reaches the remote mid-session rather than only at close. Agent tools expose status, history, on-demand commit-and-push (the workspace and any ahead project submodules), and a destructive history scrub during conversations. A tool-call guardrail blocks the agent from running destructive git commands through its bash tool, steering it to the dedicated tools instead. The extension also contributes a usage context section (scope: main + background) describing the automatic commit-on-close, the safe git surface, and the dedicated tools.

The git primitives (`commitAll`, `smartPull`/`smartPush`, the low-level `runGit` helpers, the remote-inspection helpers `fetchRemote`/`resolveRemoteDefaultBranch`/`listRemoteBranchTips`, and the shared headless-git-agent tooling) live in a neutral core module `src/git/`, not in this extension. The high-level operations are exposed to every extension through the `app.git` service, so the [projects](projects.md) and [memory](memory.md) extensions apply the same semantics to their repos without importing the git extension. This extension consumes the same core module internally and owns everything git-*workspace*-specific: the bootstrap init/sync, the agent tools, the bash guardrail, the scrub flow, and the commit post-processor.

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
| R4 | The `git-commit` post-processor (`finalize` phase) runs a headless side agent that groups changes by subdirectory/purpose and creates one descriptive commit per group; if the agent fails or leaves the tree dirty, a single deterministic commit backs it up; a clean tree is a no-op (the agent is not run) |
| R5 | The commit agent operates through a cwd-scoped `git` tool restricted to an allowlist (`status`, `diff`, `log`, `add`, `commit`, `show`) plus a read-only `read_file` tool — destructive operations are refused, and commits run with `core.editor=true` and `commit.gpgsign=false`; returned subjects are read from git (`git log`), not the agent's report. The deterministic fallback is `Update workspace files (YYYY-MM-DD)` |
| R6 | After committing, the processor pushes to `origin` via `smartPush` when the remote exists; push failures are logged and committed changes remain local |
| R7 | If uncommitted changes remain after the commit-and-push pass, the processor retries the commit once, then warns if changes still remain |
| R8 | `smartPush`: abort any stale in-progress rebase, fetch, classify divergence via merge-base ancestor checks; push directly when ahead; when diverged, attempt `rebase --autostash` then push; a conflicting rebase is handed to the agent resolver (R15) when one is wired, otherwise aborted; an unresolved conflict is aborted and surfaces as `REBASE_FAILED` with local commits preserved. When a rebase succeeds (cleanly or agent-resolved) but the subsequent push itself fails, the outcome is the distinct `PUSH_FAILED` result (logged, local commits preserved) — separate from `REBASE_FAILED` |
| R9 | `smartPull`: return `DIRTY_SKIPPED` without fetching when the tree is dirty; return `UP_TO_DATE` when the local branch is equal to *or ahead of* the remote (a local-ahead branch is treated as up-to-date — `smartPull` neither pushes nor errors in that case); fast-forward when behind; rebase when diverged; a conflicting rebase is handed to the agent resolver (R15) when one is wired, otherwise aborted; an unresolved conflict is aborted and surfaces as `SYNC_FAILED` with local state restored |
| R10 | Sync helpers resolve `"HEAD"` to the actual local branch name and follow gitlink `.git` files, so divergence detection works in repos without an `origin/HEAD` ref and inside submodules |
| R11 | Agent tools `query_git_status`, `list_recent_commits`, `commit_workspace`, and `scrub` are registered in every agent session via a pi extension factory |
| R12 | The extension can be disabled entirely via `[extensions.git] enabled = false` |
| R13 | The `scrub` tool removes given paths from the entire workspace history via `git filter-repo --invert-paths` and force-pushes to `origin`. Preconditions are checked in this order, each short-circuiting with its own result/message: empty path list (`NO_PATHS`), then dirty working tree (`DIRTY_TREE`), then paths absent from history (`PATHS_NOT_FOUND`), then `git filter-repo` not installed (`NOT_INSTALLED`). All request and state validation (input, tree state, path existence — every one a git-only check) precedes the external-tool gate, so a malformed request is reported the same way whether or not filter-repo happens to be installed. Outcomes are surfaced as a result enum, never thrown. An optional `project` name retargets the rewrite from the workspace repo to `projects/<name>`: an empty name or a non-existent project directory throws a clear error before any git operation, then the same precondition checks, rewrite, and force-push run against the project repo (and its own `origin`). Because `filter-repo` rewrites the project's commit SHAs, a successful project scrub notes that the workspace submodule pointer updates at the next session-close commit |
| R14 | A `tool_call` guardrail (registered via a second pi extension factory) blocks the agent's `bash` tool from running destructive git commands — `git push`, `git reset`, `git checkout/restore .`, `git clean`, mutating `git remote` subcommands, `git filter-repo`, and `git rebase` — and returns a message steering the agent to `commit_workspace`/`scrub`/automatic sync. Compound commands are split on shell operators (respecting quoting) and any matching sub-command blocks the whole command. Read-only git and `git clone` are not blocked |
| R15 | When `smartPush`/`smartPull` hit a rebase conflict and a `RebaseResolver` is wired (the git extension builds one from `app.agent.side`), the conflict is handed to a headless side agent that resolves the conflicted files, stages them, and continues the rebase via cwd-scoped `read_conflict`/`write_resolved`/`git` tools bound to the target repo; the `git` tool rejects push/fetch/reset/remote/filter-repo. The resolver runs in a bounded loop (at most 3 passes); completion is determined from the on-disk rebase state, not the agent's report. A resolved conflict continues the sync and surfaces as `AGENT_RESOLVED` (a member of `PUSH_SUCCESS`); an unresolved one falls back to abort (R8/R9). A thrown agent never aborts the sync |
| R16 | `commit_workspace` commits pending workspace changes, then (unless `push=false`) pushes the workspace and every registered project submodule that is ahead of its remote — including a submodule whose working tree is clean but which has local commits ahead — to `origin` via `smartPush` with the agent resolver (R15) wired. A clean tree does not skip the push: commits made earlier (e.g. a prior push that failed) are pushed too. Each repo's outcome is appended as a result line; a repo with no `origin` or one already up to date produces no line; a failing repo is reported and leaves its commits local. The push path never throws |
| R17 | Each exchange resets a debounce timer; after `[scheduler] commitDebounceMinutes` (default 5, `0` disables) of exchange quiet, the workspace is committed and pushed to `origin` in the background via the same agent-grouped commit + `smartPush` path as the close pass (R4/R6). New exchanges reset the timer; the finalize pass clears and drains it first and remains the backstop. An active conversation (exchanges within the window) defers persistence until the window elapses after the last exchange — the accepted tradeoff for not paying a commit on every exchange |

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
- Given uncommitted changes exist, when the processor runs, then the commit agent groups them into one or more descriptive commits (one per cohesive area); the working tree is clean afterwards
- Given the workspace is clean, when the processor runs, then no commit is made and no agent is run
- Given the agent fails or leaves the tree dirty, when committing, then the deterministic fallback `Update workspace files (YYYY-MM-DD)` backs a single commit and the tree still ends clean
- Given an `origin` remote is configured, when the commit completes, then `smartPush` runs and a successful push (or rebase-then-push) is logged; a failed push logs a warning and the commits remain local
- Given files changed while the commit pass ran, when the processor verifies the tree, then it commits once more and warns if changes still remain afterwards

### Debounced Mid-Session Commit-Push (R17)

Each exchange resets a debounce timer; once a configurable quiet window elapses with no further exchange, the workspace is committed and pushed in the background — so pending work reaches the remote long before the session closes, without paying a commit and push on every exchange.

**Acceptance Criteria**:
- Given exchanges keep arriving less than the debounce window apart, when each fires, then no commit or push occurs — the timer keeps resetting
- Given the debounce window elapses with no new exchange, when the timer expires, then the workspace is committed (agent-grouped, deterministic fallback) and pushed to `origin` via `smartPush` in the background, with the same outcome logging as the close pass (success → info, divergence → resolver, failure → warn)
- Given `[scheduler] commitDebounceMinutes = 0`, when exchanges arrive, then no timer is armed and nothing commits mid-session — only the session-close pass persists changes
- Given a session closes while a debounce fire is pending or in flight, when the finalize pass runs, then the pending timer is cleared (and any in-flight fire drained) so the close pass owns persistence exclusively and never races a fire
- Given the process shuts down, when the drain begins, then the pending timer is cleared (the drain's finalize handles persistence)
- Given a fire is already running, when another becomes due, then the second is deferred to a single coalesced re-run after the first completes (no overlapping executions)
- Given an active conversation (exchanges within the window), then nothing commits until the window elapses after the last exchange — a mid-conversation crash loses only the uncommitted work from that burst (recovered at the next fire or session close), the accepted tradeoff for not paying a commit on every exchange

### Divergence Handling (R8, R9, R10, R15)

`smartPush` and `smartPull` (`src/git/sync.ts`, exposed via `app.git`) replace bare `git push`/`git pull` with explicit state machines returning result enums instead of throwing.

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

### Agent Git Tools (R11, R16)

Read-only inspection plus an explicit commit-and-push, for when the user wants changes saved or published before session end.

**Acceptance Criteria**:
- Given a clean tree, when `query_git_status` runs, then it reports the current branch and "Working tree is clean."; given pending changes, the truncated porcelain listing is returned
- Given a history, when `list_recent_commits` runs with a limit, then that many commits are listed newest-first as `hash date subject`; an empty repo yields "No commits found."
- Given pending changes, when `commit_workspace` runs without a `message`, then the agent groups them into one or more descriptive commits; given an explicit `message`, it is used only as the fallback if the agent fails
- Given the workspace or a registered project submodule is ahead of its remote, when `commit_workspace` runs (default `push=true`), then it pushes via `smartPush` and appends a per-repo result line (e.g. "Pushed workspace to origin.", "Pushed project 'app' to origin."); a repo with no `origin`, or one already up to date, produces no line
- Given a clean tree with commits ahead of the remote, when `commit_workspace` runs, then it reports there is nothing to commit AND still pushes the ahead commits
- Given `push=false`, when `commit_workspace` runs, then it commits (or reports nothing to commit) without pushing
- Given a repo's push fails, when `commit_workspace` runs, then the failure is reported as a line, sibling repos still push, and the tool does not throw

### History Scrub (R13)

`scrub` is the only sanctioned history rewrite — a deliberate, destructive purge of paths from the whole history, for leaked secrets or oversized blobs.

**Acceptance Criteria**:
- Given paths that exist in history and a clean tree, when `scrub` runs, then those paths are removed from every commit via `git filter-repo --invert-paths`; with an `origin` remote, the rewritten history is force-pushed and the (filter-repo-removed) remote is restored first
- Given a repo that was scrubbed before (leaving `git filter-repo`'s per-repo state behind), when `scrub` runs again for new paths, then it completes non-interactively — no stdin prompt or hang — and purges the new paths
- Given a dirty working tree, when `scrub` runs, then it refuses with a clear message and does not rewrite anything
- Given a path absent from history, when `scrub` runs, then it reports the missing paths and does not rewrite anything
- Given `git filter-repo` is not installed, when `scrub` runs, then it returns a clear "not installed" message instead of failing opaquely
- Given the rewrite succeeds but the force-push fails, when `scrub` runs, then it reports the rewrite happened locally and the push must be retried; given no `origin` remote, the rewrite is reported as completed without a push
- Given a `project` name whose repo is clean and whose history contains the paths, when `scrub` runs, then those paths are removed from the project repo's entire history and force-pushed to the project's `origin`
- Given a non-existent project name (or an empty `project` string), when `scrub` runs, then it throws a clear error and performs no git operation
- Given a path present in the workspace but not the named project, when `scrub` runs with that `project`, then it reports the path absent from history (`PATHS_NOT_FOUND`), confirming the project repo — not the workspace — was the target

### Destructive-Git Guardrail (R14)

The guardrail keeps history mutation flowing only through the recoverable tools, so the agent cannot force-push, reset, or rewrite via raw bash.

**Acceptance Criteria**:
- Given the agent's `bash` tool is called with a destructive git command (`git push`, `git reset`, `git checkout/restore .`, `git clean`, mutating `git remote`, `git filter-repo`, `git rebase`), when the `tool_call` event fires, then the call is blocked with a reason naming the dedicated tools
- Given a non-destructive command (`git status`, `git log`, `git add`, `git commit`, `git fetch`, `git clone`, `ls`, …), when the `tool_call` event fires, then the call passes through unchanged
- Given a compound command where one sub-command is destructive, when it is evaluated, then the whole command is blocked; given a destructive keyword that appears only inside a quoted argument, then it is not blocked
- Given a non-bash tool call, when the `tool_call` event fires, then the guardrail ignores it

## Notes

- The [skill-evolution](skill-evolution.md) proposal pipeline is the only branch-publishing path outside this extension's commit/smartPush flows: a scoped headless agent authors one `skill-evolution/*` branch per proposal in worktrees under the OS temp dir, pushes create-only, and the host verifies from `ls-remote` and sweeps the namespace. It shares the core git plumbing (`src/git/agent-tools.ts`, `src/git/remote.ts`), not this extension's processors
- The bash guardrail blocking `git push` in agent sessions is why the skill-evolution proposal agent gets a purpose-built, allowlisted `git` tool instead of bash (the same pattern as the workspace commit agent)

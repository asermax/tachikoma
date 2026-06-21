# Projects

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

External code repositories managed as git submodules under `projects/` in the workspace. The agent registers, lists, and deregisters projects mid-conversation through pi tools; registered projects are synchronized on startup, their git state is injected before every prompt, and dirty projects are committed and pushed automatically when a session closes — just before the workspace commit, so submodule pointer updates land in the same pass (see [git-workspace.md](git-workspace.md)). A project's git history can also be purged via the `scrub` tool's optional `project` parameter (see [git-workspace.md](git-workspace.md)). A project that is clean but has commits ahead of its remote (for example, commits left by a background task or an earlier exchange) is also pushed automatically at session close, and on demand via the `commit_workspace` tool (see [git-workspace.md](git-workspace.md) R16).

Git authentication (SSH keys, tokens) is the user's responsibility: the system assumes credentials are configured externally and surfaces failures as tool errors or warning logs.

## User Stories

- As a user, I want to register external repositories during a conversation so that the assistant can read and modify them inside its workspace
- As a user, I want registered projects synced on every startup so that the assistant works against up-to-date code
- As a user, I want project changes committed and pushed automatically at session close so that my work is preserved without manual git operations
- As a user, I want to deregister projects I no longer need, with a guard against losing uncommitted work

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Manage external repositories as git submodules under `projects/` in the workspace; git state (`.gitmodules` plus `git submodule status`) is the registry — no database table |
| R1 | `register_project` tool adds a submodule at `projects/<name>` and checks it out to its remote default branch (remote HEAD reference, falling back to `git remote show origin`, then `main`) — never detached HEAD |
| R2 | Registration validates inputs (non-empty name and url, no duplicate directory) and cleans up partial state best-effort when the add fails |
| R3 | `deregister_project` tool removes a submodule completely (deinit, `git rm`, drop the `.git/modules` clone); with uncommitted changes it refuses unless `force=true` |
| R4 | `list_projects` tool reports each project's branch (or detached short commit) and uncommitted-change count |
| R5 | Project tools are registered in every agent session via a pi extension factory (`app.agent.use`) |
| R6 | On startup, the `sync-projects` bootstrap hook ensures `projects/` exists and syncs every registered submodule in parallel: init → default-branch checkout → `smartPull`, with one retry per submodule and per-submodule error isolation; a rebase conflict during `smartPull` is handed to an agent resolver (`createGitResolver`) before falling back to abort |
| R7 | A `projects` context section (`app.agent.use(provideContext(() => buildProjectsContext(root, log), "projects"), { sessionScopes: ["main", "background"] })`) is injected once per session in both main and background as a hidden message: usage guidance plus a session-start snapshot of each project with its branch (or detached state) and dirty count; when none are registered, guidance pointing at `register_project` |
| R8 | A `preFinalize` post-processor commits each dirty project via the agent-driven grouped flow — the agent reads recent `git log` plus any `CONTRIBUTING.md`/`CLAUDE.md`/`AGENTS.md` and matches the project's own commit style, falling back to a deterministic dated message on failure — then pushes via `smartPush`, in parallel with per-project error isolation; a rebase conflict during `smartPush` is handed to an agent resolver before falling back to abort (local commits preserved) |
| R9 | The projects processor runs before the workspace commit (`finalize` phase) so submodule pointer updates land in the same workspace commit pass |
| R10 | The extension can be disabled entirely via `[extensions.projects] enabled = false` |
| R11 | At session close, the `projects-commit` post-processor also pushes any clean project that is ahead of its remote (e.g. commits left by a background task or an earlier exchange) via `smartPush`, so committed changes never linger unpushed across sessions |
| R12 | Each exchange resets a debounce timer; after `[scheduler] commitDebounceMinutes` (default 5, `0` disables) of exchange quiet, every dirty registered project is committed and pushed to its `origin` in the background — reusing the same agent-grouped commit + `smartPush` path as the close pass (R8). New exchanges reset the timer; the close pass (R8/R11) clears and drains it first and remains the backstop |

## Behaviors

### Project Registration (R1, R2)

During a conversation, the agent registers a project by adding it as a git submodule checked out to its default branch.

**Acceptance Criteria**:
- Given a conversation is active, when the agent calls `register_project` with a name and git URL, then the repository is added as a submodule at `projects/<name>`, recorded in `.gitmodules`, and checked out to its remote default branch (not detached HEAD)
- Given an empty `name` or `url`, when the tool runs, then it fails with `'name' is required` / `'url' is required` before touching git state
- Given `projects/<name>` already exists, when the tool runs, then it fails with an "already exists" error
- Given the submodule add or checkout fails (invalid URL, unreachable or unauthenticated remote), when the tool runs, then any partial state is removed best-effort and the error surfaces the underlying git message
- Given a successful registration, when the workspace status is checked, then `.gitmodules` and the new submodule appear as uncommitted changes for the next workspace commit pass; the tool response says so explicitly

### Project Deregistration (R3)

Removal is complete — including the `.git/modules` clone — with a safety check for uncommitted work.

**Acceptance Criteria**:
- Given a registered project with a clean tree, when `deregister_project` is called, then the submodule is deinitialized, removed via `git rm`, and `.git/modules/projects/<name>` is deleted
- Given a project with uncommitted changes, when called without `force`, then the tool fails listing the porcelain status and instructing `force=true`
- Given a project with uncommitted changes, when called with `force=true`, then the project is removed and the changes are lost
- Given an unregistered name, when called, then the tool fails with a "not found" error

### Listing and Context Injection (R4, R5, R7)

The same per-project state line backs the `list_projects` tool and the `projects` context section, so the agent always knows which projects exist and whether they are dirty.

**Acceptance Criteria**:
- Given no projects are registered, when `list_projects` runs, then it reports the empty state; when the `projects` context section is built, then it still emits the usage guidance noting nothing is registered — so the agent knows the tools exist
- Given a registered project on `main` with one dirty file, when listed, then the line reads `- app: main — 1 uncommitted change`
- Given a project in detached HEAD, when listed, then the location is the short commit hash, e.g. `abc1234 (detached)`
- Given gathering state for one project fails, when the section is built, then that project is excluded from the snapshot with a warning log; given listing submodules fails entirely, the section degrades to usage guidance plus the empty-state note

### Startup Sync (R6)

The `sync-projects` bootstrap hook brings every registered submodule up to date before the first conversation.

**Acceptance Criteria**:
- Given no submodules are registered, when the hook runs, then it creates `projects/` (idempotent) and completes as a no-op
- Given the workspace was freshly cloned on a new machine, when the hook runs, then each submodule is initialized, populated, and checked out to its default branch
- Given an initialized submodule behind its remote, when the hook runs, then `smartPull` fast-forwards it to the remote head
- Given an initialized submodule whose remote and local have diverged with a conflicting rebase, when the hook runs, then `smartPull` hands the conflict to the agent resolver (`createGitResolver`); a successful resolution surfaces as `AGENT_RESOLVED`, and an unresolvable conflict is aborted to a clean tree with local commits intact
- Given a submodule has uncommitted changes, when the hook runs, then init and default-branch checkout still execute unconditionally (the hook has no dirty guard); only the `smartPull` step detects the dirty tree and returns `DIRTY_SKIPPED` with a warning, so the local commits are not rebased away
- Given a submodule sync fails, when the first attempt errors, then the full sequence retries once; a second failure is logged and the remaining submodules continue (startup is never aborted)

### Session-Close Commit and Push (R8, R9, R11)

The `projects-commit` post-processor preserves work in every dirty project before the workspace commit runs, and pushes any clean project that still sits ahead of its remote so committed changes never linger.

**Acceptance Criteria**:
- Given a project with uncommitted changes, when the session closes, then the commit agent groups them into descriptive commits that match the project's own style (reading recent `git log` plus any `CONTRIBUTING.md`/`CLAUDE.md`/`AGENTS.md`), then pushes to `origin` via `smartPush`
- Given the agent fails, when committing, then the deterministic fallback `Update <name> files (YYYY-MM-DD)` backs a single commit and the push still proceeds
- Given a project with a clean tree and no local commits ahead of its remote, when the processor runs, then it is left untouched and no agent is run
- Given a project with a clean tree but local commits ahead of its remote (e.g. committed by a background task), when the session closes, then those commits are pushed via `smartPush` without recommitting — no `commitAll`, no agent
- Given a clean project that is behind, diverged-without-local-ahead, on a detached HEAD, or has no `origin` remote-tracking ref, when the processor runs, then it is not pushed by the ahead pass (only strictly-ahead projects are pushed)
- Given the remote has conflicting divergence, when the processor runs, then `smartPush` hands the conflict to the agent resolver (`createGitResolver`); a successful resolution surfaces as `AGENT_RESOLVED` and pushes, while an unresolvable conflict (or any push failure) is aborted to a clean tree with a warning logged and the commits remaining local (retried by the next startup sync)
- Given `smartPush` returns `NOTHING_TO_PUSH` (the project was already up-to-date with the remote), when the processor checks the result, then because `NOTHING_TO_PUSH` is *not* in `PUSH_SUCCESS` (which is `PUSHED`/`REBASE_SUCCEEDED`/`AGENT_RESOLVED`), the same "push failed — changes remain committed locally" warning is logged even though nothing was actually wrong
- Given multiple dirty projects, when the processor runs, then they are processed in parallel and one project's failure does not affect the others
- Given the processor completes, when the `finalize`-phase workspace commit runs, then the updated submodule pointers are committed alongside other workspace changes ([git-workspace.md](git-workspace.md))

### Debounced Mid-Session Commit-Push (R12)

Each exchange resets a debounce timer shared with the workspace's; once a configurable quiet window elapses with no further exchange, every dirty registered project is committed and pushed in the background — so project work reaches its remote mid-session, without a commit and push on every exchange.

**Acceptance Criteria**:
- Given exchanges keep arriving less than the debounce window apart, when each fires, then no project commit or push occurs — the timer keeps resetting
- Given the debounce window elapses with no new exchange, when the timer expires, then every dirty project is committed (agent-grouped, deterministic fallback) and pushed to its `origin` via `smartPush` in the background, with the same per-project error isolation and outcome logging as the close pass
- Given `[scheduler] commitDebounceMinutes = 0`, when exchanges arrive, then no timer is armed and nothing commits mid-session — only the session-close pass persists project changes
- Given a session closes while a debounce fire is pending or in flight, when the close pass runs, then the pending timer is cleared (and any in-flight fire drained) so the close pass owns persistence exclusively
- Given the workspace and projects debounce timers fire near-simultaneously, then a workspace fire may transiently commit a submodule pointer one window stale, but the next fire or the close pass (which runs projects before the workspace) advances it — final consistency is guaranteed by the close pass

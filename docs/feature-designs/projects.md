# Design: Projects

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/projects.md](../feature-specs/projects.md)
**Status**: Current

## Purpose

This document explains how external repositories become workspace-resident projects: why submodules are the registry, how the extension reuses the git extension's primitives, and how the pipeline phases keep submodule pointers consistent with the workspace history.

## Problem Context

The workspace is a single git repo holding memories, context, and configuration ([git-workspace.md](git-workspace.md)). Users also want the assistant to work on their real codebases, which must stay independent repos with their own remotes — yet the set of registered projects has to survive restarts and travel with the workspace when it is cloned on another machine.

**Constraints:**
- The registry must live inside the workspace repo itself, not in the database — a fresh clone plus startup must reconstruct every project
- pi has no MCP; agent-facing operations register through pi's `ExtensionAPI` (`registerTool`) per [DES-001](../design/DES-001-unified-extension-api.md)
- Startup and session close run unattended: per-project error isolation, deterministic fallbacks, no prompts
- Project repos must end up on a real branch — `git submodule` checkouts default to detached HEAD, which would strand later commits

**Interactions:**
- Reuses the core git module (`src/git/`), not the git extension: high-level `commitAll`/`smartPull`/`smartPush` through the `app.git` service, and low-level `runGit`/`runGitCapture` imported from `src/git/git.ts` directly (see [git-workspace.md](git-workspace.md) and [DES-001](../design/DES-001-unified-extension-api.md))
- Post-processing phase ordering: `projects-commit` (`preFinalize`) must finish before `git-commit` (`finalize`) so pointer bumps are captured ([conversation-loop.md](conversation-loop.md) covers session close)
- Context provider and post-processor run through the DES-001 pipelines with host-level error isolation

## Design Overview

One extension (`src/extensions/projects/index.ts`) wires four pieces onto the app: a bootstrap hook (startup sync), a `projects` context section (usage + session-start state snapshot, scoped main + background), a pi extension factory (the three tools), and a `preFinalize` post-processor (commit + push). All git interrogation lives in `src/extensions/projects/git.ts`; there is no projects table — `listSubmodules` (a core primitive in `src/git/git.ts`, re-exported here) parses `git submodule status --recursive`, so `.gitmodules` and the submodule working trees are the single source of truth.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/projects/index.ts` | `defineExtension` wiring; honors `enabled` config flag | Registers hook, context section, tools factory, and processor; no logic of its own |
| `src/extensions/projects/git.ts` | Submodule plumbing: init/add/remove, default-branch resolution, `ProjectState` + `describeProjectState` (listing is the core `listSubmodules`, re-exported from `src/git/git.ts`) | One state-line format shared by the tool and the context section; dirty count derived from porcelain output |
| `src/extensions/projects/hooks.ts` | `syncProjects` bootstrap: ensure `projects/`, init → checkout default branch → `smartPull` per submodule | `Promise.allSettled` parallelism; whole sequence retried once per submodule; failures logged, never abort startup; no dirty guard — init and checkout run unconditionally, and only `smartPull` returns `DIRTY_SKIPPED` for a dirty tree; `smartPull` carries the resolver so a rebase conflict is agent-resolved (`AGENT_RESOLVED`) before aborting |
| `src/extensions/projects/context-provider.ts` | `buildProjectsContext`: the `projects` section content (usage + session-start state snapshot) | Always returns the usage guidance (plus an empty-state note when nothing is registered), so the agent knows `register_project` exists; per-project failures excluded with a warning |
| `src/extensions/projects/processor.ts` | `projects-commit` post-processor (`preFinalize`): commit + push dirty projects, then push clean projects that are ahead of their remote | Two passes: dirty (`commitAll` + `smartPush`) and clean-ahead (`smartPush` only, no commit); ahead detection is fetch-free via `detectDivergence` against the last-known remote-tracking ref (see Key Decision); push outcome checked against `PUSH_SUCCESS` — which excludes `NOTHING_TO_PUSH`; both passes parallel with per-project isolation and both carry the extension's `createGitResolver` resolver so rebase conflicts are agent-resolved (`AGENT_RESOLVED`) before aborting |
| `src/extensions/projects/tools.ts` | `register_project`, `deregister_project`, `list_projects` handlers and the pi factory | Handlers exported standalone; registration cleans up partial state on failure; deregistration guards dirty trees behind `force` |

## Key Decisions

### Git submodules as the project registry

**Choice**: Register projects as git submodules under `projects/`; derive all project knowledge from git state rather than a database table.
**Why**: The workspace repo is already synced to a remote, so `.gitmodules` makes the registry portable for free — cloning the workspace and running the `sync-projects` bootstrap reconstructs every project. Submodule pointers also give the workspace history a record of which project revision each session worked against.
**Alternatives Considered**:
- Plain clones tracked in a drizzle table: registry diverges from disk, doesn't travel with the workspace remote
- `.gitmodules` plus a mirror table for metadata: two sources of truth to reconcile

**Consequences**:
- Pro: Zero schema; registration is one git operation; survives re-clones
- Pro: Workspace commits capture pointer updates as auditable history
- Con: Submodule UX sharp edges (detached HEAD, `.git/modules` residue) must be handled explicitly — hence the checkout and `removeSubmodule` logic

### Default-branch resolution chain

**Choice**: Resolve a project's default branch as `refs/remotes/origin/HEAD` symbolic ref → `git remote show origin` HEAD line → `"main"`, then check it out explicitly after add/init.
**Why**: `git submodule update --init` leaves a detached HEAD; commits made there would be lost on the next sync. The local symbolic ref is cheap but only exists in cloned repos, so the network fallback covers fresh adds, and `"main"` is the last resort.
**Alternatives Considered**:
- Hardcoding `main`: breaks on `master` or trunk-named repos
- Asking the user per registration: not viable unattended

**Consequences**:
- Pro: Projects always sit on a real branch the session-close push can target
- Con: `git remote show` adds a network round-trip on first registration

### Reuse the core git module via `app.git`

**Choice**: Consume the high-level git operations (`commitAll`, `smartPull`, `smartPush`) through the `app.git` service, and import the low-level `runGit`/`runGitCapture`/`listSubmodules` helpers from the core module `src/git/git.ts` directly. Neither path touches `src/extensions/git/`.
**Why**: The git primitives are pure functions over a `cwd` — the same divergence handling and message generation must behave identically for the workspace and for every project. They were extracted out of the git extension into a neutral core module so consumers no longer depend on another extension; the high-level ops are surfaced through `app.git` (DES-001) because they are genuinely cross-cutting, while the thin subprocess helpers stay plain importable utilities.
**Consequences**:
- Pro: One implementation of sync/commit semantics; fixes apply everywhere
- Pro: No extension→extension coupling — projects works the same whether or not the git extension is registered
- Con: AppContext gains a service surface (`app.git`) that must be kept in lock-step with the core module's high-level functions

### Tool handlers split from the pi factory

**Choice**: Export `handleRegisterProject` / `handleDeregisterProject` / `handleListProjects` taking a narrow `ProjectToolDeps`, with `createProjectsToolsFactory` as a thin `pi.registerTool` wrapper.
**Why**: Per [DES-002](../design/DES-002-extension-authoring.md), logic takes its dependencies as narrow parameters so tests can exercise it without a pi session. `tests/projects/` runs the handlers against real temp-dir git repos, no LLM or pi runtime involved.
**Consequences**:
- Pro: Full tool behavior (cleanup on failure, force guard) covered by fast vitest suites
- Con: A thin layer of registration boilerplate per tool

### Fetch-free ahead detection at session close

**Choice**: The session-close processor runs a second pass over clean submodules, pushing any that are ahead of their remote. It detects "ahead" with `detectDivergence` against the last-known remote-tracking ref — no `git fetch` — and only submodules classified `AHEAD` trigger a `smartPush` (which then fetches and re-classifies, so a genuinely diverged-with-local-ahead submodule still gets rebased and pushed).
**Why**: Session close is automatic and frequent; fetching every clean submodule on every close would be wasteful. Classifying against the already-known `origin/<branch>` ref is a cheap local check sufficient to decide "is there something local worth pushing" — and `smartPush`'s own fetch makes the final call. The on-demand `commit_workspace` tool fetches-and-pushes every submodule on each call because it is user-initiated; the session-close path trades a tiny staleness window for far less network I/O.
**Alternatives Considered**:
- Fetch every clean submodule at close (as `commit_workspace` does): simpler, but pays a fetch per submodule per close on the automatic, frequent path
**Consequences**:
- Pro: Clean up-to-date submodules incur no push attempt and no fetch at close; only ahead ones pay for a `smartPush`
- Con: A submodule whose `origin/<branch>` ref is absent (never fetched / no remote) reads as not-ahead and is skipped that session; the startup sync's `smartPull` fetch establishes the ref, so this only affects a brand-new, never-synced submodule until the next session
- Con: A clean submodule that is behind or diverged-without-local-ahead is intentionally not pushed (nothing local to push) and is left to the startup sync or the on-demand tool

## System Behavior

### Scenario: Registration fails midway

**Given**: `register_project` is called with a URL whose clone or checkout fails
**When**: The error is raised after `git submodule add` created partial state
**Then**: `removeSubmodule` runs best-effort to clean up, and the tool error carries the original git message — no half-registered project remains.

### Scenario: Workspace re-cloned on a new machine

**Given**: The workspace was cloned fresh and `.gitmodules` lists projects
**When**: The `sync-projects` bootstrap hook runs
**Then**: Each submodule is initialized, populated, checked out to its default branch, and pulled — in parallel, with one retry each; failures log and startup continues.

### Scenario: Dirty project at session close with a diverged remote

**Given**: A project has uncommitted changes and its remote gained commits
**When**: `projects-commit` runs in `preFinalize`
**Then**: Changes are committed with a generated message, `smartPush` fetches, rebases the local commits on top of the remote, and pushes. If the rebase conflicts, the resolver (`createGitResolver`, a headless agent) drives it to completion and pushes (`AGENT_RESOLVED`); if it cannot resolve it, the push is abandoned (`REBASE_FAILED`), the commits stay local, and the next startup sync retries.

### Scenario: Clean project ahead of its remote at session close

**Given**: A project has a clean working tree but local commits ahead of `origin` (made by a background task or an earlier exchange)
**When**: `projects-commit` runs in `preFinalize`
**Then**: The dirty pass skips it (clean tree); the ahead pass detects it via `detectDivergence` and pushes the commits with `smartPush`. If the remote has also advanced (true divergence), `smartPush` fetches, rebases, and pushes; if the rebase conflicts, the resolver (`createGitResolver`) drives it to completion and pushes (`AGENT_RESOLVED`), or — if it cannot — the push is abandoned (`REBASE_FAILED`), the commits stay local, and the next startup sync retries.

## Notes

- Tests set `GIT_ALLOW_PROTOCOL=file` (`tests/projects/helpers.ts`) because the CVE-2022-39253 hardening blocks file-protocol submodule clones, and the spawned clone only honors the env allowlist
- Conflicting rebases during project sync are handed to an agent resolver (`createGitResolver`, see [git-workspace.md](git-workspace.md)): a headless side agent drives the rebase to completion through cwd-scoped `read_conflict`/`write_resolved`/`git` tools, bounded to a few attempts. A resolved conflict pushes/pulls as `AGENT_RESOLVED`; an unresolvable one aborts to a clean tree (`REBASE_FAILED`/`SYNC_FAILED`) with local state intact for the next attempt. The extension wires this resolver into its startup `smartPull` and both session-close `smartPush` passes (dirty and clean-ahead)
- The processor pushes `HEAD`, which `smartPush` resolves to the actual branch name — this is why the startup checkout to a real branch matters

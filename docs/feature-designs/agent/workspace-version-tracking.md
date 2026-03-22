# Design: Workspace Version Tracking

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/agent/workspace-version-tracking.md](../../feature-specs/agent/workspace-version-tracking.md)
**Status**: Current

## Purpose

This document explains the design rationale for workspace version tracking: how the git module initializes repos, spawns commit agents, pushes to remotes, and integrates with the post-processing pipeline.

## Problem Context

Workspace changes (memories, context files, configuration) happen as side effects of post-processing — forked LLM agents autonomously read/write files during memory extraction. Without version tracking, there's no history, no diff, and no rollback capability.

**Constraints:**
- Must run after all other post-processors complete (memory extraction writes files the git processor needs to see)
- Must not depend on gitpython — the agent uses bash git commands directly
- Must work on a fresh workspace with no prior git history
- No global git config dependency — committer identity configured per-repo

**Interactions:**
- Post-processing pipeline: git processor registers in the `finalize` phase (see [pipeline design](post-processing-pipeline.md))
- Workspace bootstrap: git hook registers after workspace hook (see [workspace-bootstrap design](workspace-bootstrap.md))
- Memory extraction processors: their file writes are what the git processor commits
- Projects processor ([project-management design](project-management.md)): runs in `pre_finalize` phase, commits and pushes submodule changes before GitProcessor. The resulting submodule reference changes appear in `git status` and are committed by GitProcessor alongside other workspace changes — no code change to GitProcessor is needed

## Design Overview

Three independent components, plus a system prompt section:

1. A **system prompt preamble** "Commits" section (`context/loading.py`) that instructs the assistant not to manually commit or push — all version control is automated
2. A **git bootstrap hook** that initializes the workspace as a git repo on first run (idempotent)
3. A **git post-processor** that spawns a lightweight Haiku agent to inspect, group, and commit workspace changes after each session, then pushes to the `origin` remote when one is configured

The post-processor runs in the pipeline's **finalize phase**, ensuring all memory extraction is complete before commits happen.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/git/__init__.py` | Re-exports: `git_hook`, `GitProcessor` | Clean public API for the git package |
| `src/tachikoma/git/hooks.py` | `git_hook`: initializes workspace as git repo | Subsystem-owned hook pattern (DES-003); uses `asyncio.create_subprocess_exec` |
| `src/tachikoma/git/processor.py` | `GitProcessor(PostProcessor)` + `GIT_COMMIT_PROMPT` + `query_and_consume` helper + `_has_remote`/`_push` helpers | Prompt co-located with processor; fresh `query()` (not fork); push helpers local to module |

### Cross-Layer Contracts

```mermaid
sequenceDiagram
    participant Pipeline as PostProcessingPipeline
    participant Git as GitProcessor
    participant Agent as Haiku Agent (query)
    participant Remote as origin remote
    participant FS as Workspace Files

    Note over Pipeline,FS: Finalize phase (after main-phase processors complete)
    Pipeline->>Git: process(session)
    Git->>Git: git status --porcelain
    alt workspace dirty
        Git->>Agent: query(prompt, model="haiku")
        Agent->>FS: git add + git commit (per group)
        Agent-->>Git: complete
        Git->>Git: git remote get-url origin
        alt origin exists
            Git->>Remote: git push origin HEAD
            Remote-->>Git: success / failure (logged)
        else no origin
            Git->>Git: skip push (debug log)
        end
        Git->>Git: git status --porcelain (verify)
    else workspace clean
        Git-->>Pipeline: no-op
    end
    Git-->>Pipeline: complete
```

**Integration Points:**
- GitProcessor ↔ subprocess: `asyncio.create_subprocess_exec("git", "status", "--porcelain")` for dirty check and post-agent verification; `git remote get-url origin` for remote detection; `git push origin HEAD` for pushing
- GitProcessor ↔ SDK: `query(prompt=GIT_COMMIT_PROMPT, options=ClaudeAgentOptions(model="haiku", cwd=..., permission_mode="bypassPermissions"))` — fresh stateless call, not a session fork
- Bootstrap ↔ git hook: `git_hook` runs after workspace hook, uses `asyncio.create_subprocess_exec` for `git init`, `git config`, `git commit`

**Error contract:**
- Git hook failures propagate as `BootstrapError` (fail-fast, per DES-003)
- GitProcessor failures caught by pipeline's `asyncio.gather(return_exceptions=True)` (error isolation)
- Partial commits are valid — if the agent commits 1 of 3 groups then fails, those commits persist
- Push failures are caught and logged as warnings — commits remain intact and will be pushed on the next session

### Shared Logic

- **`query_and_consume` function** (`git/processor.py`): standalone helper for fresh `query()` calls (no session fork). Local to the git module since no other processor currently needs this pattern.

## Modeling

The domain model is minimal — no persistent entities or state. The git processor is stateless; all state lives in the workspace filesystem and git history.

```
GitProcessor(PostProcessor)
├── _cwd: Path
└── process(session) → None

git_hook(ctx: BootstrapContext) → None

query_and_consume(prompt, cwd) → None
```

## Data Flow

### Bootstrap: git repo initialization

```
1. __main__.py registers git_hook after workspace hook
2. bootstrap.run() executes hooks in registration order
3. git_hook(ctx) runs:
   a. Read workspace_path from ctx.settings_manager.settings
   b. Check if workspace_path / ".git" exists
      ├─ exists → return immediately (idempotent)
      └─ doesn't exist → continue
   c. Run: git init
   d. Run: git config user.name "Tachikoma"
   e. Run: git config user.email "tachikoma@local"
   f. Run: git commit --allow-empty -m "Initial commit"
   g. If any subprocess returns non-zero → raise with stderr output
```

### Git post-processor: commit and push flow

```
1. GitProcessor.process(session) called during finalize phase
2. Run: git status --porcelain (from workspace cwd)
   ├─ empty output → log debug, return (no-op)
   └─ non-empty → continue
3. Spawn: query(prompt=GIT_COMMIT_PROMPT, options=ClaudeAgentOptions(
       model="haiku", cwd=self._cwd, permission_mode="bypassPermissions"))
4. Consume all messages from the async iterator
5. Run: git remote get-url origin (remote detection)
   ├─ exit code 0 (origin exists) → continue to push
   └─ non-zero (no origin) → log debug "no origin remote configured", skip push
6. Run: git push origin HEAD
   ├─ success → log info "pushed workspace changes"
   └─ failure → log warning with error details, continue
7. Run: git status --porcelain (verification)
   ├─ empty → log debug "all changes committed"
   └─ non-empty → log warning "uncommitted changes remain after git processor"
```

## Key Decisions

### Fresh query() instead of fork_and_consume

**Choice**: The git processor uses a fresh `query()` call, not `fork_and_consume` with session forking.
**Why**: The git agent doesn't need conversation history — it only needs to inspect the workspace filesystem and run git commands. A fresh call is simpler, cheaper (no forked context), and avoids coupling to the user's session.

**Consequences**:
- Pro: Cheaper per-run (no conversation context in prompt)
- Pro: Simpler — no session dependency
- Con: Can't reference conversation content in commit messages (acceptable)

### query_and_consume local to git module

**Choice**: Place the `query_and_consume` helper in `git/processor.py`, not in `post_processing.py`.
**Why**: Only one consumer (GitProcessor). If another processor needs fresh queries later, the helper can be promoted.

**Consequences**:
- Pro: Keeps `post_processing.py` focused on shared pipeline mechanism
- Pro: Git module is self-contained

### Python-side dirty check before spawning agent

**Choice**: Run `git status --porcelain` via subprocess before deciding whether to spawn the agent.
**Why**: Checking `git status` is near-instant and avoids agent cost for clean workspaces. Most sessions produce changes, but trivial sessions shouldn't incur LLM cost.

**Consequences**:
- Pro: Zero cost for clean workspaces
- Con: Duplicates the dirty check (Python checks, agent also sees status) — acceptable

### Model "haiku" with no resource limits

**Choice**: Use `model="haiku"` with no `max_turns` or `max_budget_usd`.
**Why**: Cheapest available model for a mechanical task. The task is naturally bounded (finite workspace changes).

**Consequences**:
- Pro: Simplest configuration, no risk of stopping mid-commit
- Con: Theoretically unbounded cost in pathological cases (mitigated by Haiku's low cost)

### Remote detection via `git remote get-url origin`

**Choice**: Check for the `origin` remote specifically using `git remote get-url origin` (local-only, no network call).
**Why**: More precise than `git remote` (which lists all remotes). The `origin` remote is the conventional default and matches how `ProjectsProcessor` targets submodule remotes.

**Consequences**:
- Pro: Near-instant, no network call
- Pro: Specific to `origin` — avoids accidentally pushing to an unexpected remote
- Con: Won't push if the user configured a remote with a different name (acceptable — `origin` is conventional)

### `git push origin HEAD` instead of bare `git push`

**Choice**: Use `git push origin HEAD` to push the current branch to a same-named branch on origin.
**Why**: A bare `git push` depends on `push.default` config and upstream tracking. `origin HEAD` is explicit and works even when the user hasn't set up tracking (e.g., freshly added remote with no upstream configured).

**Consequences**:
- Pro: Works without upstream tracking configuration
- Pro: Explicit — no ambiguity about which remote or branch
- Con: Slightly different from `projects/git.py:push()` which uses bare `git push` (acceptable — submodules always have tracking set up from clone)

### Push is Python-side, not agent-side

**Choice**: The commit agent prompt continues to prohibit `git push`. The processor handles pushing after the agent completes.
**Why**: Keeps the agent focused on the mechanical commit task. Push is a single command that doesn't need LLM reasoning. Matches the `ProjectsProcessor` pattern where push is done by the processor, not the commit agent.

**Consequences**:
- Pro: Agent prompt stays simple and focused on commit grouping
- Pro: Push failure handling is in Python (structured logging, exception handling) rather than relying on agent behavior
- Con: None significant

### Git package with separate hook and processor modules

**Choice**: `src/tachikoma/git/` package with `hooks.py` and `processor.py`.
**Why**: Separates bootstrap concerns from runtime concerns. Follows the `memory/` package pattern.

**Consequences**:
- Pro: Clear separation, consistent with existing patterns
- Con: More files for a small feature (acceptable)

## System Behavior

### Scenario: Session ends with workspace changes and origin configured

**Given**: Memory extraction processors wrote files to `memories/episodic/`, `memories/facts/`; origin remote is configured
**When**: The finalize phase runs the git post-processor
**Then**: `git status --porcelain` detects changes. Haiku agent groups by subdirectory and creates separate commits. Processor detects origin remote and pushes. Info log emitted.

### Scenario: Session ends with workspace changes, no origin remote

**Given**: Memory extraction processors wrote files; no origin remote configured
**When**: The finalize phase runs the git post-processor
**Then**: Changes are committed by agent. Processor detects no origin remote, logs at debug level, skips push.

### Scenario: Session ends with no changes

**Given**: Memory extraction found nothing to extract
**When**: The finalize phase runs the git post-processor
**Then**: `git status --porcelain` returns empty. Processor returns without spawning an agent or checking for remote.

### Scenario: Push fails (non-fast-forward)

**Given**: Origin remote has advanced since last push
**When**: Processor attempts `git push origin HEAD` after committing
**Then**: Push fails. Warning logged with error details. Commits remain intact locally and will be included in the next session's push.

### Scenario: Agent commits some groups but fails mid-way

**Given**: Agent commits episodic changes but crashes before committing facts
**When**: The git processor resumes after agent failure
**Then**: Episodic commits persist. Warning logged. Facts changes picked up on next run.

### Scenario: First launch — no git repo

**Given**: Workspace exists but has no `.git` directory
**When**: Bootstrap runs the git hook
**Then**: Repo initialized, identity configured, initial empty commit created. No `.gitignore`.

### Scenario: Subsequent launch — git repo exists

**Given**: Workspace has `.git`
**When**: Bootstrap runs the git hook
**Then**: Hook returns immediately (idempotent).

## Notes

- The git processor establishes a second post-processor pattern: fork-based (memory) vs. fresh-query (git). Future processors can follow either pattern.
- Agent guardrails (safe git commands only) are enforced via prompt instructions, consistent with memory processors' file scope constraints.
- No `.gitignore` is created — all workspace content is tracked by default. Users can add their own if desired.

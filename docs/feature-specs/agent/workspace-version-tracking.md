# Workspace Version Tracking

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Automatic git version tracking for all workspace file changes. Every modification to memories, context files, and configuration is recorded with descriptive commits after each session, providing built-in history and rollback capability. A git post-processor runs in the pipeline's finalization phase — after all other processors complete — spawning a lightweight agent to group changes into cohesive commits.

## User Stories

- As a user, I want workspace changes automatically version-tracked so that I can review what changed and roll back if needed
- As the system, I need automatic git version tracking so that every workspace modification is recorded with descriptive commits

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
| R8 | Bootstrap does not create a .gitignore — all workspace content is tracked by default |

## Behaviors

### Git Post-Processor (R1, R2, R3, R4)

After all main-phase processors complete (memory extraction writes files), the git post-processor checks for uncommitted changes and spawns a Haiku agent to create cohesive commits.

**Acceptance Criteria**:
- Given uncommitted changes exist in the workspace, when the git post-processor runs, then it spawns a Haiku agent via a fresh `query()` call to analyze and commit workspace changes
- Given changes span multiple subdirectories, when the agent analyzes the diff, then it creates separate commits per cohesive group (e.g., episodic memories in one commit, facts in another)
- Given all changes belong to a single subdirectory, when the agent analyzes the diff, then it creates a single commit with a descriptive message
- Given no uncommitted changes exist, when the git post-processor runs, then it completes as a no-op without spawning an agent
- Given the agent completes, when the post-processor verifies the workspace, then it logs a warning if uncommitted changes remain
- Given the agent commits some groups but fails mid-way, then partial commits remain as valid history and uncommitted changes are picked up on the next run

### Commit Agent Behavior (R3, R7)

The Haiku agent inspects the workspace and creates well-organized commits using safe git commands only.

**Acceptance Criteria**:
- Given the agent is spawned, then it uses only `git status`, `git diff`, `git add`, and `git commit` — no destructive commands
- Given the agent creates commits, then each commit message is descriptive and reflects the content of the group
- Given the agent creates commits, then it does not create or switch branches (linear history)

### Git Repo Initialization (R5, R6, R8)

A bootstrap hook initializes the workspace as a git repo on first run.

**Acceptance Criteria**:
- Given no `.git` directory in the workspace, when the git bootstrap hook runs, then a git repo is initialized with an initial empty commit
- Given a fresh init, when the hook completes, then repo-local `user.name` and `user.email` are configured with a fixed identity
- Given a fresh init, when the hook completes, then no `.gitignore` file is created
- Given an existing `.git` directory, when the hook runs, then it skips initialization (idempotent)
- Given git init fails, when the hook runs, then a clear exception propagates with the failure reason

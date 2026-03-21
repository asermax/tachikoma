# Project Management

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

External code repository management via git submodules. Users can register, track, modify, and contribute to multiple codebases alongside the assistant's workspace. Projects are stored as git submodules under a `projects/` directory, synced on every startup, and automatically committed and pushed at session end. A context provider injects project awareness at session start and provides MCP tools for registration and deregistration during conversations.

Git authentication (SSH keys, personal access tokens, etc.) is the user's responsibility — the system assumes credentials are configured externally and reports clear errors on authentication failure.

## User Stories

- As a user, I want to register external code repositories so that the assistant can track, modify, and contribute to them
- As a user, I want my projects synced on every startup so that I always work against up-to-date code
- As a user, I want changes committed and pushed automatically at session end so that my work is preserved without manual git operations
- As a user, I want to deregister projects I no longer need so that the workspace stays clean

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Manage external code repositories as git submodules for multi-codebase workflows |
| R1 | Register new projects by name and git URL during conversations (MCP tool); submodule is checked out to default branch (per remote HEAD reference), not detached HEAD |
| R2 | Store projects as git submodules within a `projects/` directory in the workspace |
| R3 | On startup, initialize and pull all submodules in parallel (1 retry on failure, log and continue) |
| R3.1 | After pull, check out the default branch (per remote HEAD reference, not detached HEAD) |
| R4 | At session end, commit and push changes in each submodule before the main workspace commit |
| R4.1 | Generate descriptive commits for submodule changes using the same approach as workspace version tracking |
| R4.2 | Push after commit; on push failure, log and continue |
| R4.3 | Process multiple submodules without sequential bottleneck |
| R5 | Main workspace git history tracks submodule reference updates (existing GitProcessor runs after projects processor) |
| R6 | Inject project context and MCP tools at session start via pre-processing context provider; MCP tools are always available even when no projects are registered |
| R7 | Deregister/remove a project (MCP tool); warn and require confirmation when uncommitted changes exist |

## Behaviors

### Project Registration (R1, R2)

During a conversation, the agent can register a new project by adding it as a git submodule. The submodule is checked out to its default branch.

**Acceptance Criteria**:
- Given a conversation is active, when the agent calls the `register_project` tool with a name and git URL, then the repository is added as a git submodule under `projects/<name>` in the workspace
- Given a project is registered, when the submodule is added, then it is checked out to its default branch (determined by the remote's HEAD reference), not detached HEAD
- Given a freshly registered project, when the submodule is added, then it contains a clean checkout of the remote's default branch with no local modifications
- Given a project name that already exists in `projects/`, when `register_project` is called, then the tool returns an error indicating the project already exists
- Given an invalid git URL or unreachable remote, when `register_project` is called, then the tool returns an error with a clear message and no partial state is left behind (cleanup on failure)
- Given a git URL requiring authentication the user has not configured, when `register_project` is called, then the tool returns an error indicating authentication failure
- Given a project is successfully registered, when the workspace git status is checked, then the `.gitmodules` file and `projects/<name>` entry are present as uncommitted changes (the next GitProcessor run will commit them)

### Startup Sync (R3, R3.1)

On startup, the bootstrap hook creates the `projects/` directory and syncs all registered submodules in parallel.

**Acceptance Criteria**:
- Given registered submodules exist in `.gitmodules`, when the application starts, then all submodules are initialized, checked out to their default branch, and pulled to latest in parallel (1 retry per submodule on failure, log and continue)
- Given a submodule pull fails, when the first attempt fails, then it retries once before logging the failure and continuing with other submodules
- Given a submodule pull succeeds, when the pull completes, then the submodule is checked out to its default branch (determined by remote's HEAD reference, not left in detached HEAD)
- Given a submodule has unpushed local commits that conflict with remote changes, when the startup pull runs, then the pull failure is logged with details about the conflict and the submodule is left in its pre-pull state
- Given no submodules are registered, when the bootstrap hook runs, then it completes as a no-op after creating the `projects/` directory
- Given some submodules fail to pull after retry, when the bootstrap completes, then the application starts normally with successful submodules available and failures logged
- Given the `projects/` directory already exists, when the bootstrap hook runs, then it does not recreate it (idempotent)
- Given a submodule's remote requires authentication the user has not configured, when the pull fails, then the failure message indicates authentication failure

### Session-End Commit and Push (R4, R4.1, R4.2, R4.3, R5)

At session end, the projects post-processor commits and pushes changes in each dirty submodule, running in the `pre_finalize` phase before GitProcessor.

**Acceptance Criteria**:
- Given uncommitted changes exist in a submodule, when the projects post-processor runs, then descriptive commits are generated for the submodule's changes (using the same approach as workspace version tracking)
- Given multiple submodules have uncommitted changes, when the projects post-processor runs, then all submodules are processed in parallel
- Given a submodule has commits to push, when commits complete, then the processor pushes to the submodule's remote
- Given a push fails for a submodule, when the error occurs, then it is logged and other submodules continue processing normally; local commits remain intact
- Given a submodule's remote has advanced since last pull, when push fails with non-fast-forward, then the failure is logged with a message indicating the remote has diverged (changes remain committed locally and will be reconciled on next startup pull)
- Given the projects post-processor completes (with or without errors), when the existing GitProcessor runs in the finalize phase, then the resulting submodule reference changes appear in `git status` and are committed alongside other workspace changes
- Given no submodules have uncommitted changes, when the projects post-processor runs, then it completes as a no-op without spawning any agents
- Given no submodules are registered, when the projects post-processor runs, then it completes as a no-op
- Given the commit generation fails for a submodule, when the error occurs, then it is logged and the processor continues with other submodules (no push attempted for the failed one)

### Project Context Injection (R6)

At session start, the context provider injects project awareness and provides MCP tools for project management. The MCP tools are always available — even when no projects are registered — so the agent can register its first project.

**Acceptance Criteria**:
- Given registered projects exist, when a new session starts, then the pre-processing context provider injects a context block listing all projects with their names, paths, and current branch, along with MCP tools for project management
- Given no projects are registered, when a new session starts, then the context provider returns a guidance message ("No projects registered. Use register_project to add one.") with MCP tools available
- Given a project's directory is missing or corrupted, when the context provider runs, then it logs a warning and excludes that project from the context block
- Given a project's submodule is in detached HEAD state, when the context provider runs, then it reports the short commit hash instead of a branch name (e.g., `abc1234 (detached)`)
- Given a session transition occurs, when the coordinator handles the transition, then MCP servers from the previous session are cleared and re-extracted from pre-processing results in the new session

### Project Deregistration (R7)

During a conversation, the agent can deregister a project, with a safety check for uncommitted changes.

**Acceptance Criteria**:
- Given a registered project with no uncommitted changes, when the agent calls the `deregister_project` tool with the project name, then the submodule is fully removed (deinit, remove from `.gitmodules`, remove directory)
- Given a registered project with uncommitted local changes, when `deregister_project` is called without the force flag, then the tool returns a warning listing the uncommitted changes and requires the `force` flag to proceed
- Given a registered project with uncommitted local changes, when `deregister_project` is called with the force flag, then the submodule is removed and the changes are lost
- Given a project name that does not exist, when `deregister_project` is called, then the tool returns an error indicating the project was not found
- Given a project is deregistered, when the workspace git status is checked, then the removal changes are present as uncommitted changes (the next GitProcessor run will commit them)

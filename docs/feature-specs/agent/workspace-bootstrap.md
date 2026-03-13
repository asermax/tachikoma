# Workspace Bootstrap

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A formal workspace initialization process that runs on every launch. Modules register idempotent setup hooks that execute in registration order. Each hook self-determines whether it needs to act, making the process safe to run every time. The workspace root and internal `.tachikoma/` data folder are created by a standard hook (not special-cased logic). Hooks can prompt users for input and persist values to the configuration file.

## User Stories

- As a developer, I want a formal initialization process so that modules can register setup steps without coupling to `__main__.py`
- As the system, I want idempotent hooks so that startup is resilient to partial failures

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Formal workspace initialization that runs module hooks on every launch |
| R1 | Internal data folder (`.tachikoma/` inside workspace) for internal state |
| R2 | Hook system with explicit registration |
| R3 | Each hook self-determines whether it needs to run (idempotent) |
| R4 | Hooks can prompt the user for input via terminal during setup |
| R5 | Hooks can persist user-provided values back to the configuration file |
| R6 | Hook execution follows explicit registration order |
| R7 | Fail fast on hook failure with clear error naming the failing hook |
| R8 | Bootstrap always runs the full hook sequence; hooks skip themselves if already initialized |

## Behaviors

### Workspace Creation (R0, R1)

On launch, the workspace hook creates the workspace root directory and `.tachikoma/` data folder if they don't exist.

**Acceptance Criteria**:
- Given no workspace directory exists, when the app starts, then the workspace root and `.tachikoma/` data folder are created
- Given the workspace already exists, when the app starts, then no directories are re-created and no errors occur
- Given the workspace path resolves to an existing regular file, when the app starts, then it exits with a clear error
- Given the workspace path's parent directory is not writable, when the app starts, then it exits with a clear error

### Hook Execution (R2, R6, R8)

Registered hooks execute in registration order on every launch. Hooks that detect they're already initialized skip themselves.

**Acceptance Criteria**:
- Given hooks are registered with explicit ordering, when the bootstrap runs, then hooks execute in registration order
- Given all hooks detect they're already initialized, when the bootstrap runs, then startup completes quickly with no side effects
- Given a hook is invoked, when it inspects its context, then it has access to settings (including the data folder path) via the settings manager
- Given zero hooks are registered, when the bootstrap runs, then it completes successfully

### Hook Idempotency (R3)

Each hook self-determines whether it needs to act, ensuring the bootstrap is safe to run every time.

**Acceptance Criteria**:
- Given a previous launch was interrupted mid-initialization, when the app is restarted, then completed hooks skip themselves and remaining hooks run normally

### User Input and Settings Persistence (R4, R5)

Hooks can prompt users for input and persist values to the configuration file.

**Acceptance Criteria**:
- Given a hook needs user input, when the hook runs, then it prompts the user via the terminal and uses their response
- Given a hook updates a setting value, when the value is saved, then the configuration file is updated and the change persists across restarts
- Given a hook updates a setting during bootstrap, when subsequent hooks run, then they see the updated value

### Registered Hooks

The following hooks are registered in `__main__.py` and execute in registration order:

**Acceptance Criteria**:
- Given the workspace hook, when it runs, then the workspace root directory and `.tachikoma/` data folder are created if they don't exist
- Given the logging hook, when it runs, then loguru is configured with structured file output under `.tachikoma/logs/`, creating the `logs/` directory if it doesn't exist
- Given the context hook, when it runs, then core context files are initialized and the system prompt is assembled
- Given the memory hook, when it runs, then `memories/`, `memories/episodic/`, `memories/facts/`, and `memories/preferences/` directories are created if they don't exist
- Given the session recovery hook, when it runs, then sessions left open from ungraceful shutdowns are detected and closed

### Failure Handling (R7)

Hook failures abort startup immediately with clear error messaging.

**Acceptance Criteria**:
- Given a hook raises an error, when the bootstrap is running, then startup aborts with a clear error message naming the failing hook
- Given a hook failed on previous launch, when the app is restarted, then the failed hook re-runs due to idempotency

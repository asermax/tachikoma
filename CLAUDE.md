# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Tachikoma is a proactive personal assistant built on Claude Agent SDK. It maintains persistent memory across conversations, extracts learnings automatically, and processes background tasks during idle time. Accessible via Telegram or a local REPL.

## Commands

All commands are available as `just` recipes. **Always use them to validate changes** — run `just check` (or the relevant subset) before considering any task complete.

```bash
just install                         # uv sync --all-groups
just run                             # run the agent (REPL by default)
just run --channel telegram          # run via Telegram

just test                            # all tests (excludes @slow)
just test tests/test_coordinator.py  # single file
just test -k "test_name"            # single test by name
just test -m slow                   # slow tests only

just lint                            # ruff check
just fmt                             # ruff format
just typecheck                       # ty check
just check                           # lint + typecheck + test (all quality gates)
```

## Architecture

### Message Flow

```
User message → Channel (Telegram/REPL)
  → Coordinator.enqueue() → Coordinator.send_message()
    → Boundary detection (topic shift? resume previous session?)
    → Pre-processing pipeline (parallel: memory, projects, skills context providers)
    → Claude Agent SDK (ClaudeSDKClient per message exchange, resume-based continuity)
    → Adapter (SDK messages → AgentEvent domain types)
  → Channel renders response
  → Per-message post-processing (summary extraction)
  → On session close: Post-processing pipeline (parallel: episodic/facts/preferences/context extraction → git commit)
```

### Key Abstractions

- **Coordinator** (`coordinator.py`): Central orchestrator. Creates a fresh `ClaudeSDKClient` per message exchange, using `resume` for conversation continuity. Manages session lifecycle, boundary detection, and pipeline execution.
- **Channels** (`repl.py`, `telegram.py`): Consume `AsyncIterator[AgentEvent]` from the coordinator and render to the user. Channels are thin — all logic lives in the coordinator.
- **Adapter** (`adapter.py`): The only module that imports SDK message types. Maps SDK messages to domain `AgentEvent` types (`TextChunk`, `ToolActivity`, `Result`, `Error`, `Status`).
- **AgentDefaults** (`agent_defaults.py`): Centralizes common SDK construction options (cwd, cli_path, env) shared across all SDK calls.
- **Bootstrap** (`bootstrap.py`): Registry of named hooks that run in order on startup. Hooks are idempotent and self-determine whether they need to act. Subsystems expose their initialization as bootstrap hooks.
- **Pipelines**: Pre-processing (`pre_processing.py`) and post-processing (`post_processing.py`) run providers/processors in parallel with error isolation. Post-processing has phased execution (main → pre_finalize → finalize).
- **Sessions** (`sessions/`): SQLAlchemy-backed session tracking with registry pattern. Sessions have SDK session IDs, transcript paths, summaries, and support close/reopen for topic resumption.
- **Tasks** (`tasks/`): Cron-based task scheduling with session-aware (idle-gated) and background execution modes. MCP tool server for agent-driven task management.

### Subsystem Pattern

Each subsystem (memory, skills, projects, context, git, tasks) follows a consistent structure:
- A **bootstrap hook** (`hooks.py`) for initialization (registered in `__main__.py`)
- A **context provider** for pre-processing (implements `ContextProvider`)
- A **processor** for post-processing (extends `PromptDrivenProcessor` — forks the SDK session with a prompt)
- An `__init__.py` that re-exports the public API

### Configuration

TOML config at `~/.config/tachikoma/config.toml`. Settings are Pydantic models in `config.py`. Auto-generates a commented default file on first run.

### Database

SQLAlchemy async with aiosqlite. Database file lives at `{workspace}/.tachikoma/tachikoma.db`. Alembic for migrations.

## Documentation

- `docs/planning/VISION.md` — Full project vision and scope
- `docs/planning/DELTAS.md` — Feature inventory with status tracking
- `docs/feature-specs/` — Specifications per feature area
- `docs/feature-designs/` — Design rationale per feature area
- `docs/design/DES-*` — Cross-cutting design patterns (testing, logging, bootstrap hooks, prompt-driven processors)
- `docs/architecture/ADR-*` — Architecture decision records

Use `/katachi:` commands to work with the planning framework.

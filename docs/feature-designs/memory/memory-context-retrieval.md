# Design: Memory Context Retrieval

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/memory/memory-context-retrieval.md](../../feature-specs/memory/memory-context-retrieval.md)
**Status**: Current

## Purpose

This document explains the design rationale for static memory index injection: how facts and preferences `MEMORY.md` indexes are read at startup, formatted into navigable sections, and injected into foundational context so the agent always has a browseable index of stored memories without per-message processing.

## Problem Context

The previous approach ran an Opus sub-agent on every incoming message to classify relevance and search memories — an expensive approach that cost an Opus round-trip per message. Facts and preferences directories already contain `MEMORY.md` index files maintained by the memory subsystem, providing a stable, human-readable navigable entry point that can be injected directly as static context.

**Constraints:**
- Facts and preferences directories already contain `MEMORY.md` index files maintained by the memory subsystem (bootstrap hook + maintenance ticks)
- The `MEMORY.md` format (`[Name](./path.md): description`) is stable and human-readable
- Background tasks also consume memory context and need the same indexes
- The system preamble already has a `## Memories` section that can be expanded with episodic documentation
- Episodic memory search is deferred to a future MCP tool (DLT-105) — not in scope

**Interactions:**
- Memory bootstrap hook (`memory/hooks.py`): creates directory structure, ensures MEMORY.md exists, reads and stashes formatted indexes
- Context bootstrap hook (`context/loading.py`): loads foundational context, reads stashed memory indexes, appends to context list
- Coordinator: saves foundational context for new sessions
- Task executor (`tasks/executor.py`): calls `load_memory_indexes()` directly in preprocessing

## Design Overview

Memory indexes are read at startup by `memory_hook` and stashed in `ctx.extras["memory_indexes"]` as formatted sections. `context_hook` reads from there and appends them to the foundational context list alongside SOUL/USER/AGENTS.md. The coordinator saves these entries for each new session, making them available as navigable `<memory_index>` sections in the system prompt. The agent sees browseable lists with one-line descriptions and reads individual files on demand via the Read tool.

The `MemoryContextProvider` class and all supporting infrastructure have been removed. Background tasks receive memory indexes through a direct `load_memory_indexes()` call in the task executor's preprocessing, without a provider class.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/memory/index.py` | `format_memory_index(memory_type, raw_content)` and `load_memory_indexes(workspace_path)` helpers | Shared between `memory_hook` (bootstrap) and `tasks/executor.py` (runtime); parses MEMORY.md entries, skips malformed ones, returns formatted sections |
| `src/tachikoma/memory/hooks.py` | `memory_hook` reads MEMORY.md files during bootstrap, formats sections, stashes in `ctx.extras["memory_indexes"]` | Uses shared `load_memory_indexes()` helper; runs before `context_hook` in bootstrap order (DES-003: cross-hook communication via extras bag) |
| `src/tachikoma/context/loading.py` | `context_hook` reads `ctx.extras.get("memory_indexes", [])` and appends to foundational context | Minimal change — reads from extras bag that memory_hook populated |
| `src/tachikoma/context/loading.py` | `SYSTEM_PREAMLE_TEMPLATE` — expanded `## Memories` section with episodic naming conventions, retention tiers, and usage guidance | Static text expansion, no code logic changes |
| `src/tachikoma/tasks/executor.py` | Direct `load_memory_indexes()` call in `_run_preprocessing()` | No provider class; inline ContextResult creation; synchronous within error-isolated try/except block |
| `src/tachikoma/__main__.py` | Per-message pipeline registers only SkillsContextProvider | MemoryContextProvider removed |

### Cross-Layer Contracts

**Integration Points:**
- `memory_hook` → `ctx.extras["memory_indexes"]` → `context_hook` → foundational context list → coordinator saves as `SessionContextEntry`
- `load_memory_indexes()` in `tasks/executor.py` → `ContextResult` entries → enriched prompt via `assemble_context()`
- `SYSTEM_PREAMLE_TEMPLATE` → `render_system_preamble()` → `build_system_prompt()` → agent sees episodic documentation

**Error handling:**
- `memory_hook`: missing/empty MEMORY.md → skip silently; malformed entries → skip silently (logged at debug level), include well-formed ones
- `context_hook`: no `memory_indexes` in extras → skip (memory_hook ran first but found nothing)
- `tasks/executor.py`: `load_memory_indexes()` failure → caught in `_run_preprocessing()`'s existing try/except, falls back to original prompt

### Shared Logic

- **`format_memory_index(memory_type, raw_content)`** — formats a MEMORY.md file's raw content into an injectable section with header, description, and entries. Returns `None` if no well-formed entries found.
- **`load_memory_indexes(workspace_path)`** — reads and formats both facts and preferences MEMORY.md files, returns `list[tuple[str, str]]` of `(owner_tag, formatted_content)` pairs. Used by both `memory_hook` and the task executor.

## Data Flow

### Static index injection flow (at startup)

```
1. Bootstrap runs hooks in order:
   a. memory_hook runs (creates dirs, ensures MEMORY.md exists)
      - Reads memories/facts/MEMORY.md → format_memory_index("facts", content)
      - Reads memories/preferences/MEMORY.md → format_memory_index("preferences", content)
      - Stashes formatted results in ctx.extras["memory_indexes"]
   b. context_hook runs (loads foundational context)
      - Loads SOUL.md, USER.md, AGENTS.md as before
      - Reads ctx.extras.get("memory_indexes", [])
      - Appends memory index entries to foundational context list
      - Stores combined list in ctx.extras["foundational_context"]

2. Coordinator receives foundational_context from bootstrap extras

3. On new session:
   - Coordinator saves all foundational context entries (including memory indexes)
     as SessionContextEntry instances with metadata=None

4. On each message:
   - build_system_prompt() wraps entries in XML tags
   - Agent sees <memory_index>...</memory_index> sections in system prompt
   - Agent reads individual files via Read tool when index entries suggest relevance
```

### Background task preprocessing flow (at runtime)

```
1. TaskExecutor._run_preprocessing(prompt, pinned_skills)
2. Load memory indexes: load_memory_indexes(workspace_path)
   → reads and formats both MEMORY.md files
   → returns list of (tag, content) tuples
   (synchronous within _run_preprocessing()'s error-isolated try/except block)
3. Create ContextResult entries from memory indexes
4. Include in preprocessing results alongside other providers
5. assemble_context(all_results, prompt) → enriched prompt with memory indexes
```

### Episodic memory flow (preamble-based, no search)

```
1. System preamble includes expanded ## Memories section
2. Agent sees episodic naming conventions, retention tiers, content expectations
3. Agent reads episodic files manually via Read when needed
   (future: DLT-105 adds MCP tools for episodic search)
```

## Key Decisions

### Memory hook stashes indexes in extras bag

**Choice**: `memory_hook` reads and formats MEMORY.md files, stashing results in `ctx.extras["memory_indexes"]`. `context_hook` reads from there and appends to foundational context.
**Why**: Preserves subsystem ownership — memory-related logic stays in the memory module, context-related assembly stays in the context module. The extras bag is the bootstrap's built-in mechanism for cross-hook communication (DES-003).
**Alternatives Considered**:
- Extend `context_hook` directly to read MEMORY.md: mixes memory-subsystem logic into the context module, violating subsystem ownership (DES-003)
- Dedicated provider in session-gated pipeline: adds a provider class when static injection makes per-message providers unnecessary for memory

**Consequences**:
- Pro: Clean subsystem boundaries per DES-003
- Pro: `memory_hook` already ensures MEMORY.md exists, so reading it there is natural
- Pro: `context_hook` only needs one extra line to append
- Con: Subtle ordering dependency — `memory_hook` must run before `context_hook` (already guaranteed by registration order)

### Shared helper function for memory index formatting

**Choice**: Extract `format_memory_index()` and `load_memory_indexes()` into `memory/index.py` for use by both `memory_hook` and the task executor.
**Why**: Both consumers need to read and format the same MEMORY.md files with the same rendering logic. Sharing a function prevents format drift.

**Consequences**:
- Pro: Single source of truth for index formatting
- Pro: Natural home in `memory/index.py` alongside `run_index_rebuild()`
- Pro: Pure function, easy to test independently

### Direct function call in task executor (no provider)

**Choice**: The task executor calls `load_memory_indexes()` directly in `_run_preprocessing()` and creates `ContextResult` entries inline.
**Why**: Removes the context provider pattern for memory. The task executor already has its own preprocessing flow — a function call is simpler and more transparent than introducing a new provider class.

**Consequences**:
- Pro: No provider classes for memory
- Pro: Direct, readable code
- Con: Task executor has slightly more inline logic (acceptable — ~5 lines)

### Episodic memory deferred to DLT-105

**Choice**: No episodic memory injection or search in this design. The preamble documents episodic file structure so the agent can read files manually.
**Why**: Episodic files are numerous and date-organized — they don't have a MEMORY.md index. Static injection would require loading all file names, which is unbounded.

**Consequences**:
- Pro: Keeps scope focused on high-value change (eliminating Opus round-trip for facts/preferences)
- Con: Agent loses automatic episodic search until DLT-105 lands

## System Behavior

### Scenario: Normal startup with memory indexes

**Given**: A workspace with `memories/facts/MEMORY.md` and `memories/preferences/MEMORY.md` containing valid entries
**When**: The system starts up
**Then**: `memory_hook` reads both files, formats them, and stashes in extras. `context_hook` appends them to foundational context. The agent sees both sections in its system prompt.

### Scenario: Missing MEMORY.md for one directory

**Given**: A workspace where only `memories/facts/MEMORY.md` exists
**When**: The system starts up
**Then**: Only the facts index is injected. Preferences is skipped silently. No error or warning.

### Scenario: Empty MEMORY.md file

**Given**: A workspace where a MEMORY.md contains only the header
**When**: The system starts up
**Then**: `format_memory_index()` returns `None`. That directory's index is skipped. No error.

### Scenario: Malformed entries in MEMORY.md

**Given**: A MEMORY.md file with some well-formed and some malformed entries
**When**: `memory_hook` reads and formats the file
**Then**: Well-formed entries are included. Malformed entries are skipped silently (logged at debug level).

### Scenario: No memories directory at all

**Given**: A workspace where the `memories/` directory does not exist
**When**: The system starts up
**Then**: `memory_hook` creates the directory structure (existing behavior). No indexes are injected. Startup proceeds normally.

### Scenario: Background task gets memory context

**Given**: A background task is being prepared for execution
**When**: `_run_preprocessing()` runs
**Then**: The executor calls `load_memory_indexes()` directly, creates `ContextResult` entries, and includes them in the assembled context.

### Scenario: Agent reads a fact file on demand

**Given**: The agent sees the facts index with an entry like `[Restaurants](memories/facts/restaurants.md): Favorite restaurants`
**When**: The user asks "what restaurants do I like?"
**Then**: The agent uses the Read tool to read the file and responds with the content.

## Notes

- Memory indexes are static for the lifetime of a session (loaded at startup). Changes to MEMORY.md during a session (from post-processing extraction or maintenance ticks) won't be reflected until the next startup. This is acceptable because MEMORY.md changes infrequently and the agent can still read newly created files directly via Read.
- The `load_memory_indexes()` function is a pure utility — it reads files and returns formatted strings, with no dependency on the SDK, pipeline abstractions, or database.
- The formatted index sections include usage instructions ("read it with the Read tool") so the agent knows how to navigate the indexes without relying on the preamble.

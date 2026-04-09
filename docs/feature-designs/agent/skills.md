# Design: Skill System and Sub-Agent Delegation

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/agent/skills.md](../../feature-specs/agent/skills.md)
**Status**: Current

## Purpose

This document explains the design rationale for the skill system: how skills are structured, discovered, registered, detected per-message, and integrated with the coordinator to enable targeted sub-agent delegation via the SDK.

## Problem Context

The coordinator needs to make specialized sub-agents available to the SDK's orchestrator for delegation. Skills provide a structured, discoverable way to organize and define these agents. Only relevant skills should be loaded per session to avoid wasting context on irrelevant agents.

**Constraints:**
- Skills must be directory-based (not single files) to accommodate future expansion
- Agent definitions must be loadable from markdown files with metadata
- Skills must be discoverable at startup; only relevant skills loaded per-session based on LLM classification
- Skills must be refreshable at runtime when files change on disk, without restart
- Invalid or missing skills/agents should not crash the system
- Detection failures must never block messages — same error contract as other pre-processing providers
- Skills are detected and accumulated per-message within a session — newly detected skills are appended as context entries; the agent set is re-derived on each message from all accumulated entries

**Interactions:**
- Bootstrap process creates the skills directory and shared registry (via skills hook, see [workspace-bootstrap](workspace-bootstrap.md))
- Skill registry discovers all skills and agents at startup, with on-demand refresh when marked dirty
- Filesystem watcher monitors the skills directory and marks the registry dirty when changes occur
- Event bus (ADR-009) carries SkillsChanged events for future consumers (e.g., context invalidation)
- Skills context provider classifies relevance per-message via the per-message pre-processing pipeline (see [pre-processing-pipeline](pre-processing-pipeline.md))
- Coordinator derives agents from context entries and the skill registry, passing them to SDK (see [core-architecture](core-architecture.md))
- SDK's internal orchestrator uses detected agents for delegation decisions

## Design Overview

Eight-component architecture: a bootstrap hook creates the directory structure and shared registry, the skill registry discovers and loads all skills and agents at startup from multiple sources (with runtime refresh support), a filesystem watcher monitors the skills directory for changes and marks the registry dirty, a per-message pre-processing pipeline runs on every message, a skills context provider (receiving the registry via injection) classifies relevance per-message considering only skills not already in context (refreshing the registry first), the coordinator derives agents from context entries and the skill registry, and the system prompt preamble provides awareness-level skill context independent of per-message detection.

```
┌────────────────────────────────────────────────────────────┐
│              Coordinator Layer                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Coordinator                                     │      │
│  │  - Derives agents from entries + registry        │      │
│  │  - Re-derives on each message (accumulates)      │      │
│  │  - Passes agents to ClaudeAgentOptions           │      │
│  └────┬─────────────────────────────────────────────┘      │
├───────┼────────────────────────────────────────────────────┤
│       ▼                                                    │
│  ┌──────────────────────────────────────────────────┐      │
│  │  SkillsContextProvider (PerMessagePreProcessing)  │      │
│  │  - Refreshes registry before classification      │      │
│  │  - Filters to unloaded skills only               │      │
│  │  - Classifies relevance via LLM                  │      │
│  │  - One entry per skill, with metadata            │      │
│  │  - Returns content only (no agents)              │      │
│  │  - Receives SkillRegistry via injection          │      │
│  └────┬─────────────────────────────────────────────┘      │
├───────┼────────────────────────────────────────────────────┤
│       ▼                                                    │
│  ┌──────────────────────┐  ┌───────────────────────────┐   │
│  │ Skill Registry       │  │ Watcher Task              │   │
│  │ (multi-source,       │  │ (asyncio.Task)            │   │
│  │  bootstrap extras)   │  │                           │   │
│  │                      │  │ - awatch(skills/)         │   │
│  │ - Discovers skills   │  │ - Marks registry dirty    │   │
│  │   from all sources   │  │ - Dispatches SkillsChanged│   │
│  │ - Loads agents       │  │ - 5s debounce             │   │
│  │ - Last-wins on       │  │                           │   │
│  │   collision          │  │                           │   │
│  │ - Refreshes on       │  │                           │   │
│  │   dirty flag         │  │                           │   │
│  └────┬─────────────────┘  └───────────────────────────┘   │
├───────┼────────────────────────────────────────────────────┤
│       ▼                                                    │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Skill Sources                                   │      │
│  │  Built-in: src/tachikoma/skills/builtin/         │      │
│  │  ├── skill-authoring-guide/                      │      │
│  │  │   ├── SKILL.md                                │      │
│  │  │   └── references/agents.md                    │      │
│  │  Workspace: workspace/skills/                    │      │
│  │  ├── custom-skill/                               │      │
│  │  │   ├── SKILL.md                                │      │
│  │  │   └── agents/*.md                             │      │
│  │  Channel: (via get_skill_sources() + add_source) │      │
│  │  ├── src/tachikoma/telegram/skill/               │      │
│  │  │   └── SKILL.md                                │      │
│  └──────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────┘
```

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/skills/__init__.py` | Re-exports `SkillRegistry`, `Skill`, `SkillsChanged`, `SkillsContextProvider`, `skills_hook`, `watch_skills` | Package module for the skills subsystem |
| `src/tachikoma/skills/registry.py` | `SkillRegistry` class: discovers skills from multiple sources, loads agents, builds agents dict, stores skill body and path; refreshes all sources on dirty flag via swap-on-success; `add_source(path)` registers and discovers additional sources post-construction (used by channels to contribute skills during startup); `Skill` dataclass for metadata (name from folder, description, version, body, path) | Uses `python-frontmatter` for parsing; constructs `AgentDefinition` directly; multi-source with last-wins precedence; `mark_dirty()` for external callers, `refresh()` for dirty-check-and-rescan; `add_source()` appends to `_skill_sources` and calls `_discover()` immediately |
| `src/tachikoma/skills/context_provider.py` | `SkillsContextProvider(MessageContextProvider)`: extends standalone MessageContextProvider ABC (not ContextProvider — signatures are incompatible), receives `SkillRegistry` via constructor injection, refreshes registry before classification, classifies only unloaded skills via standalone `query()` with Opus low effort (DES-007), reads skill body from registry's pre-loaded `Skill.body`, returns one ContextResult per detected skill with metadata identifying skill name, agents=None. Also contains `extract_skill_names()` and `derive_agents_from_entries()` helpers | Receives registry and `AgentDefaults` via constructor injection; no tools for classification agent (pure reasoning); fully consumes query() generator (DES-005); `extract_skill_names()` reads skill names from entry metadata for filtering; `derive_agents_from_entries()` derives agents for the coordinator |
| `src/tachikoma/skills/hooks.py` | `skills_hook` bootstrap callback: creates `workspace/skills/` directory, resolves built-in skills path, creates `SkillRegistry` with both sources, stores in `ctx.extras["skill_registry"]` | Follows DES-003 pattern; built-in path via `Path(__file__).parent / "builtin"`; graceful fallback if built-in missing |
| `src/tachikoma/skills/watcher.py` | `watch_skills()` async function: monitors skills directory, marks registry dirty, dispatches `SkillsChanged` events; top-level exception handler prevents silent task death | Uses `watchfiles.awatch()` with 5s debounce and 2s rust_timeout; relies on watchfiles' default filtering behavior (hidden files, `__pycache__` excluded) |
| `src/tachikoma/skills/events.py` | `SkillsChanged(BaseEvent[None])`: typed event for skill change notification | Follows bubus event pattern (ADR-009); no payload — signals "something changed" |
| `src/tachikoma/context/loading.py` (`SYSTEM_PREAMBLE`) | Awareness-level skills documentation in the system prompt preamble: skills exist in `skills/` directory, auto-detected per session, can create/manage, distinct from Claude Code's native skills and slash commands. Structural details (SKILL.md format, agents/, YAML fields) are covered by the built-in authoring guide skill | Part of the `SYSTEM_PREAMBLE` constant; loaded once at startup; independent of per-message detection; follows ADR-008 append pattern |
| `src/tachikoma/per_message_pre_processing.py` | `MessageContextProvider` ABC (standalone, not extending ContextProvider) + `MessagePreProcessingPipeline` class (parallel execution with error isolation). Providers receive existing context entries enabling filtering of already-loaded data | New file mirroring `message_post_processing.py` pattern; pipeline runs on every message (not session-gated); has internal lock for concurrent invocation safety |

### Cross-Layer Contracts

**Bootstrap → Registry → Provider → Per-Message Pipeline → Coordinator contract:**

The skills hook creates the registry during bootstrap and exposes it via extras. The provider receives the registry via injection, refreshes it, classifies only unloaded skills, assembles one result per skill with metadata, and returns content only (no agents). The coordinator derives agents from all accumulated context entries and the skill registry.

```
skills_hook(ctx)
    │
    ├── workspace_skills_path = workspace_path / "skills"
    ├── Creates workspace_skills_path directory (idempotent)
    ├── Resolves builtin_path = Path(__file__).parent / "builtin"
    │   ├─ Exists → include in sources
    │   └─ Missing → log warning, skip
    ├── Creates SkillRegistry([builtin_path, workspace_skills_path])
    └── ctx.extras["skill_registry"] = registry

__main__.py (after bootstrap.run())
    │
    ├── skill_registry = bootstrap.extras["skill_registry"]
    ├── SkillsContextProvider(agent_defaults, skill_registry)
    └── msg_pre_pipeline.register(SkillsContextProvider)

SkillsContextProvider.provide(message, existing_entries=entries)
    │
    ├── Calls registry.refresh() (dirty check → re-scan all sources if needed)
    ├── Extracts loaded skill names from entries metadata
    ├── Filters registry to unloaded skills only
    ├── If no unloaded skills → return None (skip, no LLM call)
    ├── Classifies via query() [Opus low effort, DES-007]
    ├── For each detected skill:
    │   └── ContextResult(tag="skills", content=XML, metadata={"skill_name": name})
    └── Returns list[ContextResult] (agents=None on all)
        │
        └── Per-message pipeline collects results
                │
                ▼
        Coordinator saves new entries, derives agents from all entries + registry
                │
                └── ClaudeAgentOptions(agents=derived_agents)
```

**Watcher → Registry → EventBus contract:**

The watcher monitors the skills directory and signals the registry and event bus when changes are detected.

```
watch_skills(skills_path, registry, bus)
    │
    └── on file changes (debounced 5s):
        ├── registry.mark_dirty() → sets _dirty = True
        └── await bus.dispatch(SkillsChanged()) → notifies subscribers
```

**Integration Points:**
- skills_hook ↔ Bootstrap: registered as standard hook (DES-003); creates `SkillRegistry`, writes `"skill_registry"` to extras
- __main__.py ↔ extras: reads `"skill_registry"` after bootstrap; passes to provider constructor
- SkillsContextProvider ↔ SkillRegistry: injected dependency; provider reads `skills` property and calls `get_agents_for_skill()`
- Channel → SkillRegistry: `channel.get_skill_sources()` returns paths; `__main__.py` calls `registry.add_source(path)` for each — startup-time, one-directional integration
- SkillRegistry ↔ filesystem: scans each source path (built-in + workspace + channel-provided) for skill directories; reads `SKILL.md` and agent markdown files
- SkillsContextProvider ↔ Per-Message Pipeline: registers via `msg_pre_pipeline.register(provider)`; `provide(message, existing_entries=entries)` called on every message
- SkillsContextProvider ↔ SkillRegistry: shared — registry received via constructor from bootstrap extras; `refresh()` called before `skills` property access
- SkillsContextProvider ↔ SDK: standalone `query()` call for classification (no tools, low effort, DES-007)
- Per-Message Pipeline ↔ Coordinator: `pipeline.run(message, existing_entries=entries)` returns `list[ContextResult]`; coordinator saves new entries and derives agents from all entries + registry
- Watcher ↔ Registry: `mark_dirty()` — write-only, no return value
- Watcher ↔ EventBus: `await bus.dispatch(SkillsChanged())` — awaited dispatch, no return value used
- `__main__.py` ↔ Watcher: task creation/cancellation via `asyncio.create_task` / `task.cancel()`
- Skills hook ↔ Bootstrap: registered as a standard bootstrap hook (DES-003); creates registry with multi-source and stores in `ctx.extras`
- SYSTEM_PREAMBLE ↔ Agent: the preamble includes an awareness-level Skills section (skills exist, auto-detected, can create/manage); structural details covered by built-in authoring guide skill

## Modeling

### Agent Definition Transformation

```mermaid
flowchart TD
    File["Agent Markdown File"] --> Parse["Parse YAML frontmatter + markdown body"]
    Parse --> Extract["Extract: description, model, tools, body"]
    Extract --> Validate{"Description non-empty?"}
    Validate -->|No| Skip["Log warning, skip agent"]
    Validate -->|Yes| Create["Create AgentDefinition"]
    Create --> Dict["Add to agents dict as skill-name/agent-name"]
```

**AgentDefinition fields** (SDK type):
- `description`: From YAML frontmatter (required)
- `prompt`: From markdown body (empty string is valid)
- `model`: From YAML frontmatter (optional; recognized literals mapped through, unrecognized values default to `None` for SDK default)
- `tools`: From YAML frontmatter (optional list of tool names)

### Data Types

```
Skill (dataclass)
├── name: str (derived from folder name)
├── description: str
├── version: str | None
├── body: str (SKILL.md content without YAML frontmatter, loaded at init)
└── path: Path (absolute path to skill directory)

SkillRegistry
├── __init__(skill_sources: list[Path])  # scans each source; last-wins on name collision
├── _agents: dict[str, AgentDefinition]
├── _skills: dict[str, Skill]
├── _dirty: bool                         (set by watcher, cleared by refresh)
├── _skill_sources: list[Path]           (stored for reuse during refresh)
├── add_source(path: Path) → None       (appends to _skill_sources, discovers immediately; used by channels)
├── mark_dirty() → None                 (external API for watcher)
├── refresh() → None                    (check dirty, re-discover all sources if needed)
├── get_agents() → dict[str, AgentDefinition]
├── get_agents_for_skill(skill_name: str) → dict[str, AgentDefinition]
└── skills (property) → dict[str, Skill]

SkillsContextProvider(MessageContextProvider)   [standalone ABC, not extending ContextProvider]
├── _agent_defaults: AgentDefaults
├── _registry: SkillRegistry     (injected via constructor from bootstrap extras)
└── provide(message, *, existing_entries) → list[ContextResult] | None
    ├── Extract loaded skill names from entries metadata
    ├── Filter registry to unloaded skills only
    ├── Skip if no unloaded skills → return None
    ├── Classify via query() (DES-007)
    └── Return one ContextResult per skill, each with metadata={"skill_name": name}, agents=None

extract_skill_names(entries) → set[str]
└── Reads metadata["skill_name"] from entries where owner="skills"

derive_agents_from_entries(entries, registry) → dict[str, AgentDefinition]
└── Extracts skill names from entries, looks up agents from registry per skill

SkillsChanged(BaseEvent[None])
└── (no fields — signals "skills changed on disk")
```

## Data Flow

### Agent Discovery Process

```
1. SkillRegistry receives skill_sources: list[Path]
2. For each source path in skill_sources:
   ├─ Directory doesn't exist → skip source (debug log, valid state)
   └─ Directory exists → scan for skill subdirectories
3. For each subdirectory in source:
   a. Check for SKILL.md
      ├─ Not found → log warning, skip directory
      └─ Found → parse YAML frontmatter
   b. Derive name from folder, validate description (required)
      ├─ Invalid → log warning, skip skill
      ├─ Name collision with earlier source → remove earlier skill's agents, replace
      └─ Valid → store Skill metadata, proceed to agents
   c. Check for agents/ subdirectory
      ├─ Not found → valid skill with no agents, continue
      └─ Found → scan for .md files
   d. For each .md file in agents/:
      ├─ Parse YAML frontmatter + markdown body
      ├─ Validate (description required)
      ├─ Create AgentDefinition with namespace "skill-name/agent-name"
      └─ Add to agents dictionary
4. Return complete skills and agents dictionaries
```

### Startup Integration

```
1. Bootstrap runs skills hook:
   a. Creates workspace/skills/ directory (idempotent)
   b. Resolves built-in path (Path(__file__).parent / "builtin")
      ├─ Exists → include in sources
      └─ Missing → log warning, omit
   c. Creates SkillRegistry([builtin_path, workspace_skills_path])
      → Registry scans built-in first, then workspace (last-wins precedence)
      → Loads all SKILL.md files (including body and path) and agents/
   d. Stores registry in ctx.extras["skill_registry"]
2. __main__.py retrieves skill_registry from bootstrap.extras["skill_registry"]
3. __main__.py creates SkillsContextProvider(agent_defaults, skill_registry)
   → Provider receives registry (does not create its own)
4. __main__.py registers SkillsContextProvider in per-message pre-processing pipeline (not session-gated)
5. __main__.py creates channel, then registers channel-provided skill sources:
   for path in channel.get_skill_sources():
       skill_registry.add_source(path)
   → Skills from channel sources discovered immediately (e.g., Telegram's send_file skill)
   → Channel sources included in subsequent refresh() scans (last-wins precedence)
6. __main__.py creates watcher task:
   asyncio.create_task(watch_skills(skills_path, skill_registry, bus), name="skills_watcher")
7. __main__.py passes skill_registry and msg_pre_pipeline to Coordinator constructor
7. Detection happens per-message via per-message pipeline:
   → Provider calls registry.refresh() first (dirty check → re-scan all sources)
   → Provider extracts loaded skill names from existing entries metadata
   → Provider filters to unloaded skills only, classifies relevance via LLM
   → New entries saved with metadata, agents derived from all entries + registry
   → SDK sees accumulated agents from all detected skills
```

### Skill Change Detection and Refresh

```
1. File change occurs in workspace/skills/
   (create dir, write SKILL.md, add agent .md, modify, delete)

2. watchfiles.awatch() accumulates changes during debounce window (5s)
   └── Burst of changes coalesced into single yield

3. Watcher loop receives change set
   ├── registry.mark_dirty()          → sets _dirty = True
   └── bus.dispatch(SkillsChanged())  → notifies subscribers

4. Next per-message evaluation, coordinator calls per-message pipeline
   └── SkillsContextProvider.provide(message, existing_entries=entries)
       ├── registry.refresh()
       │   ├── _dirty is True → proceed
       │   ├── Save references: old_agents, old_skills
       │   ├── Clear dicts, run _discover(skills_path)
       │   ├── Success → reset _dirty = False
       │   └── Exception → restore old_agents, old_skills, log error
       └── Continue with classification using refreshed registry
```

## Key Decisions

### Directory-based Skills over Single Files

**Choice**: Skills are directories (`skills/skill-name/`) containing SKILL.md and agents/ subdirectory, not single files.
**Why**: Directories allow for future expansion (instructions, resources, configurations) without breaking the structure.
**Alternatives Considered**:
- Single files: Simpler but brittle; precludes adding skill-level components later

**Consequences**:
- Pro: Extensible foundation for future skill components
- Pro: Clear organizational hierarchy
- Con: More filesystem operations needed

### YAML Frontmatter for Metadata

**Choice**: Skill and agent metadata is embedded in markdown files using YAML frontmatter, parsed with the `python-frontmatter` library.
**Why**: Markdown is human-readable, and YAML frontmatter is a widely-adopted convention. Metadata stays with the file it describes, making skills self-contained and portable.
**Alternatives Considered**:
- Raw PyYAML (manual frontmatter extraction): Requires manual `---` delimiter parsing
- Separate JSON/YAML files: Decoupled but requires more files per skill

**Consequences**:
- Pro: Self-contained metadata with markdown body
- Pro: Human-friendly format, portable
- Con: Adds `python-frontmatter` dependency

### Model Type Narrowing

**Choice**: Map recognized model strings (`sonnet`, `opus`, `haiku`, `inherit`) to typed literals; default unrecognized values to `None` (SDK applies default model).
**Why**: The SDK's `AgentDefinition.model` field expects `Literal["sonnet", "opus", "haiku", "inherit"] | None`. Python's type system requires narrowing the raw YAML string to a literal. Unrecognized values become `None` rather than causing an error, keeping the registry lenient while satisfying type safety.

**Consequences**:
- Pro: Type-safe AgentDefinition construction
- Pro: No crashes from unexpected model strings
- Con: Silently defaults unrecognized models to SDK default (mitigated by warning logs)

### Skill Metadata Retention

**Choice**: SkillRegistry retains skill metadata (name, description, version) in memory after agent extraction, accessible via a `skills` property.
**Why**: Future features (automatic skill detection and injection) will need skill metadata for matching incoming messages against skills. Retaining metadata avoids rework.

**Consequences**:
- Pro: Forward-compatible without registry restructuring
- Pro: Negligible memory cost
- Con: Slightly more data in memory than strictly needed for current functionality

### Per-Message Agent Detection via Per-Message Pipeline

**Choice**: Agents are detected per-message based on message context via the skills context provider in the per-message pre-processing pipeline. Skills accumulate within a session — only unloaded skills are classified on each message.
**Why**: Per-session detection locks skills to the first message, missing newly relevant skills as the conversation topic evolves. Per-message detection with accumulation ensures skills are detected as they become relevant, while filtering already-loaded skills prevents redundant LLM calls.
**Alternatives Considered**:
- All agents at startup: Wastes context on irrelevant agents
- Per-session detection (previous approach): Simpler but misses skills that become relevant mid-session
- Per-message detection replacing all skills: Would lose previously relevant skills

**Consequences**:
- Pro: Skills detected as they become relevant during conversation
- Pro: Only unloaded skills classified — no redundant LLM calls
- Pro: Topic shifts trigger fresh classification (session boundary clears entries)
- Con: LLM call on every message until all skills are loaded (mitigated by skip when all loaded)

### Registry Created by Bootstrap Hook with Provider Injection

**Choice**: The skills bootstrap hook creates the `SkillRegistry` with multi-source support and exposes it via `ctx.extras["skill_registry"]`, shared between the provider and the filesystem watcher. The provider receives it via constructor injection.
**Why**: The hook needs to resolve multiple source paths (built-in + workspace) — an infrastructure concern that belongs in bootstrap. The provider is a consumer that shouldn't know about source paths. The registry is consumed by two components (provider and watcher), matching the established extras pattern used by database, session_registry, and task_repository.
**Alternatives Considered**:
- Provider creates registry internally: Would require the provider to know about built-in paths, mixing infrastructure and consumption concerns; doesn't support watcher access without coupling
- Module-level helper in registry.py: Keeps resolution near the registry but doesn't match the project's bootstrap extras pattern

**Consequences**:
- Pro: Consistent with existing bootstrap → extras → consumer pattern
- Pro: Provider becomes simpler — just uses the registry
- Pro: Registry is available to other consumers (watcher for hot-reload)
- Con: skills_hook gains more responsibility (directory creation + registry creation)

### Filesystem Watching with watchfiles

**Choice**: Use `watchfiles` (by the pydantic team) for filesystem monitoring, with `awatch()` as an async generator.
**Why**: Rust-backed for performance, built-in async support (`awatch()`), native debounce parameter (satisfies burst coalescing without custom logic), actively maintained. Used by `uvicorn` for auto-reload.
**Alternatives Considered**:
- `watchdog`: Pure Python, requires manual async bridge and custom debounce
- `inotify` / `asyncinotify`: Linux-only, no cross-platform support
- Built-in `pathlib` polling: No OS-level events, wasteful

**Consequences**:
- Pro: Native debounce eliminates custom coalescing logic
- Pro: `awatch()` integrates naturally with asyncio task pattern
- Con: Adds a new dependency (`watchfiles`)
- Con: Cancellation latency bounded by `rust_timeout` (mitigated: 2s)

### Dirty Flag with Swap-on-Success Refresh

**Choice**: Boolean dirty flag set by watcher, checked by provider. Refresh uses swap-on-success: save old references, re-discover all sources into fresh dicts, restore on failure.
**Why**: Minimizes coupling — watcher only sets a flag, provider controls when to re-scan. Swap-on-success ensures valid state even if `_discover()` fails.

**Consequences**:
- Pro: Simple, safe, no locking needed (single-threaded asyncio)
- Pro: Failed refresh preserves previous valid state
- Con: Brief window where dirty flag is set but not yet processed (next `provide()` picks it up)

### Skill Body and Path Stored at Registry Init Time

**Choice**: The `Skill` dataclass stores `body` (SKILL.md content without frontmatter) and `path` (directory path) at registry initialization, rather than reading from the filesystem at detection time.
**Why**: Simpler and avoids duplicate filesystem reads. The registry already reads SKILL.md for metadata — storing the body at the same time is trivial. The provider reads `skill.body` from the registry rather than re-reading from disk.
**Alternatives Considered**:
- Read SKILL.md from filesystem at detection time: Avoids storing bodies in memory but adds filesystem reads during the critical path

**Consequences**:
- Pro: Simpler — body available from the registry without additional filesystem access
- Pro: No I/O during the detection/classification flow
- Con: All skill bodies stored in memory (negligible — skill files are small)

### Graceful Error Handling

**Choice**: Invalid skills/agents are logged as warnings; registry continues loading other skills.
**Why**: A single malformed skill file should not crash the entire system. Partial functionality is better than complete failure.

**Consequences**:
- Pro: System resilience
- Pro: Operator sees what went wrong (diagnostic logging)
- Con: Silent skipping could hide typos (mitigated by explicit warning logs)

### Per-Message Pipeline over Direct Coordinator Call

**Choice**: Create `MessagePreProcessingPipeline` mirroring the existing `MessagePostProcessingPipeline`, rather than having the coordinator call the skills provider directly.
**Why**: The project already has a paired pipeline pattern — `MessagePostProcessingPipeline` runs per-message post-response. Creating the pre-response counterpart keeps the architecture symmetric. It also makes DLT-076 (memory re-evaluation) a simple registration away rather than requiring coordinator changes.

**Consequences**:
- Pro: Architectural consistency with `MessagePostProcessingPipeline`
- Pro: DLT-076 becomes a registration, not a coordinator change
- Pro: Pipeline handles parallel execution and error isolation automatically
- Con: New file + new ABC (minimal overhead)

### SkillsContextProvider Returns Content Only (No Agents)

**Choice**: `SkillsContextProvider.provide()` returns `ContextResult` with content populated but agents=None. The coordinator derives agents from entries + registry independently.
**Why**: Since entries are the source of truth, the agents field on ContextResult would be redundant — the coordinator already re-derives agents from all entries after saving new ones. Having the provider also populate agents creates a dual source of truth that could diverge.

**Consequences**:
- Pro: Single source of truth for agents (entries + registry)
- Pro: Provider becomes simpler — only responsible for skill content
- Con: ContextResult.agents field unused for skills (acceptable — field is optional)

### One Entry per Skill with Metadata

**Choice**: Each detected skill is persisted as a separate context entry with metadata={`skill_name`: name}, rather than combining all detected skills into a single entry per classification run.
**Why**: One entry per skill makes individual skill tracking clean — each entry has its own metadata, making it trivial to identify which skills are loaded. It also produces cleaner system prompt assembly and makes future features like skill-specific invalidation straightforward.

**Consequences**:
- Pro: Clean metadata per skill (one name per entry)
- Pro: Easier skill-specific queries and future invalidation
- Pro: System prompt has natural separation per skill
- Con: More entries per session (negligible — typically <10 skills)

### Entries as Single Source of Truth

**Choice**: Derive both loaded skill names and agents from context entries. Remove `self._agents` as independent coordinator state.
**Why**: Context entries are already the source of truth — they persist across messages, survive session resume, and are already loaded for system prompt assembly. Maintaining separate state creates a synchronization risk.

**Consequences**:
- Pro: Zero new coordinator state fields
- Pro: Naturally consistent — entries and agents always agree
- Pro: Session resume works automatically (entries are persisted)
- Con: Agent derivation requires registry access on every message (negligible — dict lookup)

### Registry Lookup Failure for Deleted Skills

**Choice**: When deriving agents, skill names in entries that don't exist in the registry (skill was deleted from filesystem) are silently skipped with a debug log.
**Why**: Skills can be deleted at runtime via the filesystem watcher. The entry persists (historical context), but the registry no longer has the skill. Including the deleted skill's agents would fail anyway, and logging at debug level avoids noisy warnings for a benign condition.

**Consequences**:
- Pro: Handles runtime skill deletion gracefully
- Pro: Entry content still includes the skill description (readable by the agent as context)
- Con: Agent loses access to deleted skill's agents (correct behavior)

### Standalone MessageContextProvider ABC

**Choice**: `MessageContextProvider` is a standalone ABC rather than extending the existing `ContextProvider`. The signatures are incompatible — `ContextProvider.provide()` returns `ContextResult | None` while `MessageContextProvider.provide()` returns `list[ContextResult] | None` and accepts `existing_entries`.
**Why**: Extending ContextProvider with an incompatible return type would violate the Liskov Substitution Principle. Making MessageContextProvider standalone keeps both contracts clear — providers are registered in one pipeline type or the other, not both.

**Consequences**:
- Pro: No inheritance mismatch or LSP violation
- Pro: Each ABC has a clear, focused contract
- Con: Two separate ABCs for context providers (acceptable — they serve different pipeline types)

### SkillRegistry.add_source() for Post-Construction Sources

**Choice**: Add an `add_source(path)` method to `SkillRegistry` that appends to `_skill_sources` and immediately discovers skills from the new path.
**Why**: The skill registry is created during bootstrap before the channel exists. Channels need to contribute skill sources after bootstrap. Rather than restructuring the bootstrap order, adding a method to accept additional sources post-construction is minimal and follows the existing multi-source pattern.
**Alternatives Considered**: Restructure bootstrap to include channel (over-engineering), pass channel skill paths to bootstrap hook via extras (couples bootstrap to channel selection logic)

**Consequences**:
- Pro: Clean extension point — any subsystem can add sources post-construction
- Pro: Maintains last-wins precedence — channel skills added last, can override built-in/workspace
- Note: Channel sources are not watched by the filesystem watcher (acceptable — they're package resources, not user-editable)

### Skills Provider Moves Entirely to Per-Message Pipeline

**Choice**: `SkillsContextProvider` is removed from the session-gated `PreProcessingPipeline` and registered only in the `MessagePreProcessingPipeline`.
**Why**: Running skills in both pipelines would cause double classification on the first message (session-gated + per-message both running). Moving it entirely avoids this. On the first message, the per-message pipeline runs with empty entries, producing the same result as the previous session-gated behavior.

**Consequences**:
- Pro: No double classification on first message
- Pro: Skills evaluation always goes through the same code path (unified process)
- Con: Session-gated pipeline no longer includes skills (memory and projects only)

## System Behavior

### Invariants

1. **Agent Uniqueness by Namespace**: Each agent has a unique namespace key (skill-name/agent-name). Skill names are folder names (unique by filesystem constraint) and agent names are filename stems (unique within a skill).

2. **Session Stability**: Skills accumulate per-message within a session — newly detected skills are appended as context entries. The agent set is re-derived on each message from all accumulated entries. Existing entries are never modified or removed. Detection runs on every message, classifying only unloaded skills.

3. **Graceful Degradation**: Invalid skills or agents do not cause the system to fail. Registry returns whatever agents it successfully loaded.

### Scenario: First launch — no skills exist

**Given**: The `skills/` directory is empty (created by bootstrap hook)
**When**: The registry initializes
**Then**: An empty agents dictionary is returned. The coordinator starts with no sub-agents. System operates normally.
**Rationale**: Empty registry is a valid initial state.

### Scenario: Skill with valid agents

**Given**: A skill directory with valid SKILL.md and agent definitions exists
**When**: The registry initializes
**Then**: All agents are discovered, validated, and added to the agents dictionary with namespace keys.
**Rationale**: Happy path — skills are self-contained and discoverable.

### Scenario: Mixed valid and invalid skills

**Given**: Some skills are valid and some have errors (bad YAML, missing fields)
**When**: The registry initializes
**Then**: Valid skills load normally. Invalid skills are logged as warnings and skipped. The coordinator starts with the agents from valid skills only.
**Rationale**: Graceful degradation — one bad skill shouldn't prevent others from loading.

### Scenario: Skill detection on new session (first message)

**Given**: Skills exist in the registry and a user sends a message matching one or more skills
**When**: Per-message pipeline runs with empty entries (first message of session)
**Then**: Provider classifies full registry, detects matches, reads body from registry, returns one ContextResult per skill with metadata. Entries saved. Agents derived from entries + registry.
**Rationale**: Core detection path — same unified process as subsequent messages, just with empty existing entries.

### Scenario: Subsequent message — new skill relevant

**Given**: An active session with skills A and B in context entries
**When**: A message relevant to skill C arrives
**Then**: Provider extracts {A, B} from entries, classifies against registry minus {A, B}, detects C. New entry for C appended. Agents re-derived to include C's agents alongside A and B.
**Rationale**: Incremental classification — only unloaded skills considered.

### Scenario: All skills loaded — skip evaluation

**Given**: An active session where all registry skills are already in context entries
**When**: A new message arrives
**Then**: Provider finds no unloaded skills and returns None immediately (no LLM call). No new entries saved. Existing agents unchanged.
**Rationale**: Skip when no unloaded skills remain — zero latency overhead.

### Scenario: No relevant skills detected

**Given**: Skills exist but none match the user's message
**When**: Pre-processing runs
**Then**: Classification returns no relevant skills. Provider returns None (no context block, no agents). Message proceeds with memory context only.
**Rationale**: Precision — irrelevant skills are not loaded.

### Scenario: Classification agent fails

**Given**: Provider runs but the forked Opus agent fails (SDK error, timeout)
**When**: Exception is caught
**Then**: Provider logs the error (DES-002), returns None. No agents loaded, no skills context. Other providers (memory) complete normally.
**Rationale**: Detection failures never block the message.

### Scenario: Skill change detected at runtime

**Given**: A skill is added, modified, or deleted while the application is running
**When**: The filesystem watcher detects the change (after 5s debounce), marks the registry dirty, and dispatches a SkillsChanged event
**Then**: The next per-message evaluation's `provide()` call triggers a registry refresh, discovering the updated skills. The current session's accumulated entries remain, but newly discovered skills become available for classification on the next message.
**Rationale**: Runtime refresh enables skill authoring without restart while allowing new skills to be detected mid-session through the per-message evaluation.

### Scenario: Watcher encounters an error

**Given**: The watcher is running and encounters an OS-level error (e.g., inotify watch limit exhaustion)
**When**: The exception is caught by the watcher's top-level handler
**Then**: The error is logged and the watcher task terminates. The registry retains its last known state. Skills continue to work but won't hot-reload until restart.
**Rationale**: The watcher is a best-effort enhancement — failure should not crash the application.

## Notes

- The SDK orchestrator makes delegation decisions opaquely. The application provides agents; the SDK decides how to use them.
- Tool scoping via agent definition's tools field is enforced by the SDK at invocation time.
- The classification prompt design is an implementation detail — it embeds all skill names + descriptions and the user message, asking which skills are relevant.
- The `NO_RELEVANT_SKILLS` sentinel pattern (consistent with `MemoryContextProvider`'s `NO_RELEVANT_MEMORIES`) distinguishes "classified and found nothing" from "agent error."
- `watchfiles` is a project dependency (added to `pyproject.toml`), maintained by the pydantic team (Samuel Colvin) and used by `uvicorn` for auto-reload.
- `MessageContextProvider` is a standalone ABC (not extending `ContextProvider`) because the return types are incompatible — `ContextResult | None` vs `list[ContextResult] | None`. See Key Decisions for rationale.
- `extract_skill_names()` and `derive_agents_from_entries()` live in `skills/context_provider.py` for cohesion with the skills module that owns the skill-related logic, rather than in the per-message pipeline module.
- DLT-076 (memory re-evaluation) can reuse the per-message pipeline by registering a memory provider — no coordinator changes needed.

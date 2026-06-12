# Delta Inventory

Deltas (work items) extracted from VISION.md for the Tachikoma TypeScript rewrite. This inventory is the porting roadmap: every feature area of the Python implementation is re-expressed as a delta for the pi stack, grouped by milestone. The **Ports** field traces each delta back to the original feature area it reimplements; deltas without it exist only in the rewrite.

## Status Tracking

Deltas track their progress through the development workflow using a status field:

- **✗ Pending** - Delta defined, not started (initial state for all port deltas)
- **⧗ Spec** - Specification in progress (`/spec-delta` started)
- **✓ Spec** - Specification complete (`/spec-delta` done)
- **⧗ Design** - Design rationale in progress (`/design-delta` started)
- **✓ Design** - Design complete (`/design-delta` done)
- **⧗ Plan** - Implementation plan in progress (`/plan-delta` started)
- **✓ Plan** - Implementation plan complete (`/plan-delta` done)
- **⧗ Implementation** - Delta implementation in progress (`/implement-delta` started)
- **✓ Implementation** - Delta complete and tested (`/implement-delta` done)
- **✓ Reconciled** - Feature documentation updated (`/reconcile-delta` done)
- **✓ Done** - Delta completed outside the delta workflow (used for pre-framework work)

Commands automatically update status as they progress. To manually update:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py status set DELTA-ID "STATUS"
```

## Priority Tracking

Deltas have a priority level (1-5) that determines their urgency:

| Level | Label | Description |
|-------|-------|-------------|
| 1 | Critical | Blocks the next milestone, must do now |
| 2 | High | Important, needed soon |
| 3 | Medium | Standard priority (default) |
| 4 | Low | Nice to have |
| 5 | Backlog | Someday/maybe |

Priorities follow milestone order: the active milestone's deltas are Critical/High, later milestones decay toward Medium.

---

## Milestone 1 — Core Shell + Walking Skeleton

The thin core: enough infrastructure that a user can hold an end-to-end REPL conversation with a long-lived pi session, with every later feature able to plug in as an extension.

### DLT-001: Toolchain scaffold
**Status**: ✓ Done
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The project installs, lints, formats, typechecks, tests, and runs directly from TypeScript sources (Node type stripping, pnpm, Biome, vitest, just) with the pi SDK pinned and importable.

### DLT-002: Configuration system
**Status**: ✗ Pending
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Ports**: configuration/config-system
**Description**: TOML configuration at `~/.config/tachikoma/config.toml` is parsed with smol-toml, validated and defaulted via TypeBox schemas, and auto-generated as a commented default file on first run.

### DLT-003: Structured logging
**Status**: ✗ Pending
**Depends on**: DLT-002
**Priority**: 1 (Critical)
**Complexity**: Easy
**Ports**: cross-cutting logging
**Description**: All components log through pino with per-component child loggers, pretty output in development and structured JSON in service mode.

### DLT-004: Database layer
**Status**: ✗ Pending
**Depends on**: DLT-002
**Priority**: 1 (Critical)
**Complexity**: Medium
**Ports**: cross-cutting persistence
**Description**: A shared drizzle database over `node:sqlite` lives at `{workspace}/.tachikoma/tachikoma.db`, with drizzle-kit migrations applied automatically at startup.

### DLT-005: Typed event bus
**Status**: ✗ Pending
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Ports**: agent/core-architecture (event bus)
**Description**: Extensions publish and subscribe to typed events through a core event bus without knowing about each other.

### DLT-006: Extension host
**Status**: ✗ Pending
**Depends on**: DLT-002, DLT-003
**Priority**: 1 (Critical)
**Complexity**: Hard
**Description**: Extensions defined via `defineExtension({ name, config?, setup(app) })` are loaded in order and receive an `AppContext` with app-level hooks (scheduler, db, session lifecycle, channels, context providers, post-processing) plus `app.agent.use()` for contributing pi extension factories.

### DLT-007: Workspace initialization
**Status**: ✗ Pending
**Depends on**: DLT-002
**Priority**: 1 (Critical)
**Complexity**: Easy
**Ports**: agent/workspace-bootstrap
**Description**: First run creates the workspace layout (memories, skills, core context files) compatible with the Python-era workspace, plus an isolated pi `agentDir` at `{workspace}/.tachikoma/pi`.

### DLT-008: Coordinator with long-lived pi session
**Status**: ✗ Pending
**Depends on**: DLT-006, DLT-007
**Priority**: 1 (Critical)
**Complexity**: Hard
**Ports**: agent/core-architecture (coordinator + adapter)
**Description**: The coordinator enqueues user messages, prompts a single long-lived `AgentSession` per conversation, maps `session.subscribe()` events to domain `AgentEvent`s for channels, and routes mid-generation input as steering/follow-up.

### DLT-009: Channel registry + REPL channel
**Status**: ✗ Pending
**Depends on**: DLT-008
**Priority**: 1 (Critical)
**Complexity**: Medium
**Ports**: channels/terminal-repl
**Description**: Channels register against the core and a terminal REPL channel renders streamed agent responses — the walking skeleton: message in, response out, end to end.

### DLT-010: Scheduler
**Status**: ✗ Pending
**Depends on**: DLT-006
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: A croner-based in-process scheduler exposed through `AppContext` supports timezone-aware cron and one-shot scheduling for any extension.

---

## Milestone 2 — Sessions, Boundary, Memory, Context

Conversation lifecycle and the learning loop: sessions open, close, resume, and leave memories behind.

### DLT-011: Session registry
**Status**: ✗ Pending
**Depends on**: DLT-004, DLT-008
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: agent/sessions
**Description**: Sessions are tracked in the database with their pi transcript path, summary, and lifecycle state, supporting close and reopen.

### DLT-012: Pipeline execution
**Status**: ✗ Pending
**Depends on**: DLT-006
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: agent/pre-processing-pipeline, agent/post-processing-pipeline
**Description**: Registered context providers run in parallel before a conversation and post-processors run in phases on session close, with per-item error isolation so individual failures never block the conversation.

### DLT-013: Rolling summaries
**Status**: ✗ Pending
**Depends on**: DLT-011
**Priority**: 2 (High)
**Complexity**: Easy
**Ports**: agent/sessions (per-message post-processing)
**Description**: After each agent response, a side-channel `complete()` call updates the session's rolling summary used by boundary detection.

### DLT-014: Boundary detection
**Status**: ✗ Pending
**Depends on**: DLT-013
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: agent/boundary-detection
**Description**: Each incoming message is classified via a side-channel LLM call as continuing the current topic, starting a new one, or matching a recent closed session to resume.

### DLT-015: Session replacement on boundary
**Status**: ✗ Pending
**Depends on**: DLT-014
**Priority**: 2 (High)
**Complexity**: Hard
**Ports**: agent/sessions (close/reopen)
**Description**: On a detected boundary the coordinator closes the current session (triggering post-processing) and swaps in a new or resumed `AgentSession` via `AgentSessionRuntime`, rebinding extensions and event subscriptions.

### DLT-016: Cold-start session resumption
**Status**: ✗ Pending
**Depends on**: DLT-015
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: agent/sessions (cold-start resumption)
**Description**: The first message after a process restart is matched against recent closed sessions and resumes the matching conversation instead of starting cold.

### DLT-017: Session idle timeout
**Status**: ✗ Pending
**Depends on**: DLT-011, DLT-010
**Priority**: 2 (High)
**Complexity**: Easy
**Ports**: agent/sessions (idle timeout)
**Description**: Sessions idle beyond a configurable period auto-close so post-processing runs without requiring a topic shift.

### DLT-018: Context extension
**Status**: ✗ Pending
**Depends on**: DLT-006, DLT-007
**Priority**: 2 (High)
**Complexity**: Easy
**Ports**: agent/core-architecture (core context files)
**Description**: SOUL.md, USER.md, and AGENTS.md from the workspace are composed into the pi session's system prompt, re-read on session start.

### DLT-019: Memory extraction
**Status**: ✗ Pending
**Depends on**: DLT-012, DLT-015
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: memory/memory-extraction
**Description**: On session close, parallel processors read the pi JSONL transcript and extract episodic summaries, facts, and preferences into type-organized markdown files in the workspace.

### DLT-020: Memory context retrieval
**Status**: ✗ Pending
**Depends on**: DLT-012
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: memory/memory-context-retrieval
**Description**: A context provider surfaces relevant stored memories at conversation start so the agent references past interactions without being asked.

### DLT-021: Core context updates
**Status**: ✗ Pending
**Depends on**: DLT-018, DLT-019
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: agent/core-context-updates
**Description**: A post-processor extracts high-confidence refinements from the closed conversation and applies them to SOUL.md, USER.md, and AGENTS.md.

---

## Milestone 3 — Skills, Workflows, Tasks

Packaged expertise and autonomous work.

### DLT-022: Skills via progressive disclosure
**Status**: ✗ Pending
**Depends on**: DLT-006, DLT-007
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: agent/skills
**Description**: The workspace skills directory is contributed as a pi skill source (`resources_discover`), making Agent Skills-format packages available through pi's native progressive disclosure — no LLM classification pass.

### DLT-023: Skill hot-reload
**Status**: ✗ Pending
**Depends on**: DLT-022
**Priority**: 3 (Medium)
**Complexity**: Easy
**Ports**: agent/skills (hot-reload)
**Description**: Changes to the skills directory are detected and the pi resource set reloaded so new or edited skills are available without restarting.

### DLT-024: Skill-bundled agents
**Status**: ✗ Pending
**Depends on**: DLT-022
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: agent/skills (agent definitions)
**Description**: Agent definitions bundled inside skill packages are discovered and exposed as delegable subagents the main agent can hand focused work to.

### DLT-025: Skill authoring guide
**Status**: ✗ Pending
**Depends on**: DLT-022
**Priority**: 3 (Medium)
**Complexity**: Easy
**Ports**: built-in skills
**Description**: A built-in skill teaches the agent to scaffold new skills with correct structure and metadata.

### DLT-026: Workflow engine
**Status**: ✗ Pending
**Depends on**: DLT-004, DLT-006
**Priority**: 2 (High)
**Complexity**: Hard
**Ports**: workflows/workflow-state-machine
**Description**: Directory-based workflow definitions execute as database-persisted step state machines (pending, started, completed, skipped) with `registerTool` lifecycle tools to start, advance, query, end, and list workflows.

### DLT-027: Stale workflow cleanup
**Status**: ✗ Pending
**Depends on**: DLT-026, DLT-012
**Priority**: 4 (Low)
**Complexity**: Easy
**Ports**: workflows (cleanup processor)
**Description**: A post-processor expires workflow instances abandoned across sessions.

### DLT-028: Workflow authoring guide
**Status**: ✗ Pending
**Depends on**: DLT-026, DLT-022
**Priority**: 3 (Medium)
**Complexity**: Easy
**Ports**: built-in skills
**Description**: A built-in skill teaches the agent to create workflow definitions with steps, references, and scripts.

### DLT-029: Task management
**Status**: ✗ Pending
**Depends on**: DLT-004, DLT-010
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: tasks/task-management
**Description**: Tasks are defined with timezone-aware cron or one-shot schedules, survive restarts with catch-up for missed runs, and are managed conversationally via `registerTool` tools.

### DLT-030: Session task execution
**Status**: ✗ Pending
**Depends on**: DLT-029, DLT-008
**Priority**: 2 (High)
**Complexity**: Medium
**Ports**: tasks/session-task-execution
**Description**: Due session tasks wait for a configurable idle window, then inject their prompt into the active session (`sendUserMessage` with turn trigger) so results arrive as proactive messages without interrupting conversation.

### DLT-031: Background task execution
**Status**: ✗ Pending
**Depends on**: DLT-029
**Priority**: 2 (High)
**Complexity**: Hard
**Ports**: tasks/background-task-execution
**Description**: Background tasks run autonomously in their own in-memory pi sessions, iterating with a completion evaluator until done and producing a summary of what was accomplished.

---

## Milestone 4 — Channels (Telegram Full), Projects, Git

The primary interface and workspace versioning.

### DLT-032: Telegram channel
**Status**: ✗ Pending
**Depends on**: DLT-009
**Priority**: 2 (High)
**Complexity**: Hard
**Ports**: channels/telegram
**Description**: A grammY-based Telegram channel renders streamed responses with message splitting, tool-activity markers, and steering/stop handling for text conversations.

### DLT-033: Telegram media support
**Status**: ✗ Pending
**Depends on**: DLT-032
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: channels/telegram (media)
**Description**: Incoming photos, audio, voice, documents, stickers, video, video notes, and animations are downloaded with size validation and temp-file lifecycle management and passed to the agent.

### DLT-034: Telegram push notifications
**Status**: ✗ Pending
**Depends on**: DLT-032
**Priority**: 3 (Medium)
**Complexity**: Easy
**Ports**: channels/telegram (push notifications)
**Description**: Configurable push alerts notify the user when the agent has responded or a background task completed.

### DLT-035: Projects extension
**Status**: ✗ Pending
**Depends on**: DLT-012
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: agent/project-management
**Description**: External repositories register as tracked projects, synchronize on startup, and surface their state plus register/deregister tools via a context provider.

### DLT-036: Project commit/push post-processor
**Status**: ✗ Pending
**Depends on**: DLT-035
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: agent/project-management (session-close commits)
**Description**: On session close, changes in each registered project are committed with descriptive messages and pushed to their remotes.

### DLT-037: Workspace git versioning
**Status**: ✗ Pending
**Depends on**: DLT-012
**Priority**: 2 (High)
**Complexity**: Easy
**Ports**: agent/workspace-version-tracking
**Description**: The workspace is committed automatically after each session, creating a history of all memory and context changes for rollback and auditing.

### DLT-038: Workspace git sync
**Status**: ✗ Pending
**Depends on**: DLT-037
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: agent/workspace-version-tracking (git sync)
**Description**: Workspace and project repositories synchronize via fetch-rebase-push with divergence detection and conflict recovery instead of bare push.

---

## Milestone 5 — Detached Processes, Notifications, External Extensions, Polish

Supervision, delivery coordination, third-party extensibility, and release readiness.

### DLT-039: Notifications extension
**Status**: ✗ Pending
**Depends on**: DLT-005
**Priority**: 3 (Medium)
**Complexity**: Medium
**Ports**: notifications
**Description**: Event-bus-driven notifications with severity levels flow from producers (background tasks, process watchers) to the active channel, including an agent-facing notify tool for background runs.

### DLT-040: Buffered delivery
**Status**: ✗ Pending
**Depends on**: DLT-039, DLT-030
**Priority**: 3 (Medium)
**Complexity**: Hard
**Ports**: delivery/priority-buffer
**Description**: A priority buffer with idle-window and max-hold timeouts coordinates notification and session-task delivery so proactive messages land at natural conversation pauses without being lost or interleaved.

### DLT-041: Detached process supervision
**Status**: ✗ Pending
**Depends on**: DLT-006
**Priority**: 3 (Medium)
**Complexity**: Hard
**Ports**: detached-processes/process-supervision
**Description**: Tools dispatch, monitor, and control OS-level commands that outlive the session, with watchers tracking exit status and streaming output back to the agent.

### DLT-042: External extension loading
**Status**: ✗ Pending
**Depends on**: DLT-006
**Priority**: 3 (Medium)
**Complexity**: Hard
**Ports**: plugins/plugin-loading (superseded: the manifest-based plugin system collapses into loading out-of-tree defineExtension modules, plus git install tooling)
**Description**: Third-party Tachikoma extensions declared in configuration are loaded through the same `defineExtension` contract as first-party ones — the single out-of-tree extensibility path.

### DLT-043: Granular processing status
**Status**: ✗ Pending
**Depends on**: DLT-012, DLT-009
**Priority**: 4 (Low)
**Complexity**: Easy
**Ports**: diagnostics (status messages)
**Description**: Component-driven status updates during pre-processing and shutdown post-processing replace a generic "Thinking..." indicator in channels.

### DLT-044: Release pipeline
**Status**: ✗ Pending
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Ports**: distribution/release-pipeline
**Description**: Conventional commits drive semantic versioning, changelog generation, and installable releases.

# Delta Inventory

Deltas (work items) extracted from VISION.md for Tachikoma.

## Status Tracking

Deltas track their progress through the development workflow using a status field:

- **✗ Defined** - Delta extracted and documented (initial state)
- **⧗ Spec** - Specification in progress (`/spec-delta` started)
- **✓ Spec** - Specification complete (`/spec-delta` done)
- **⧗ Design** - Design rationale in progress (`/design-delta` started)
- **✓ Design** - Design complete (`/design-delta` done)
- **⧗ Plan** - Implementation plan in progress (`/plan-delta` started)
- **✓ Plan** - Implementation plan complete (`/plan-delta` done)
- **⧗ Implementation** - Delta implementation in progress (`/implement-delta` started)
- **✓ Implementation** - Delta complete and tested (`/implement-delta` done)
- **✓ Reconciled** - Feature documentation updated (`/reconcile-delta` done)

Commands automatically update status as they progress. To manually update:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py status set DELTA-ID "STATUS"
```

Query status:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py status list                    # All deltas
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py status list --complexity Easy  # Filter by complexity
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py status show DELTA-ID           # Detailed view
```

## Priority Tracking

Deltas have a priority level (1-5) that determines their urgency:

| Level | Label | Description |
|-------|-------|-------------|
| 1 | Critical | Blocks release, must do now |
| 2 | High | Important, needed soon |
| 3 | Medium | Standard priority (default) |
| 4 | Low | Nice to have |
| 5 | Backlog | Someday/maybe |

Set priority:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py priority set DELTA-ID LEVEL
```

List by priority:
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py priority list                  # Grouped by priority
python ${CLAUDE_PLUGIN_ROOT}/scripts/deltas.py priority list --level 1        # Filter by level
```

---

## Deltas

### DLT-002: Send and receive messages via Telegram
**Status**: ✓ Design
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Integrate with the Telegram Bot API to provide the primary user-facing communication channel for v1. Users send text messages to a Telegram bot, which forwards them to the coordinator agent. The coordinator's responses are sent back through the same channel. This delta covers the full Telegram lifecycle: bot initialization, receiving incoming messages via polling or webhooks, forwarding them into the agent architecture, sending responses back, and managing the connection (including reconnection on disconnects and graceful shutdown). Message validation ensures only expected input reaches the agent.

### DLT-003: Delegate tasks to focused sub-agents
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Enable the coordinator to delegate specialized requests to focused sub-agents instead of handling everything itself. When a request requires specific expertise or tooling, the coordinator spawns a sub-agent that receives only the context and tools relevant to its task — preventing context poisoning and keeping each agent sharp. The sub-agent executes, returns its result, and the coordinator synthesizes it into a user-facing response. This delta covers the delegation mechanism itself (how to spawn, scope, and collect results from sub-agents); specific sub-agent types are defined by their own deltas. Error handling for sub-agent failures (timeouts, crashes, bad output) should be addressed as part of this mechanism.

### DLT-004: Detect conversation boundaries via inactivity timeout
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Fallback conversation boundary detection that monitors for periods of user inactivity. After a configurable threshold (~20 minutes by default), the system signals the session registry (DLT-027) to close the current session, triggering downstream post-processing. This serves as a safety net for cases where the user goes silent without a clear topic change — DLT-026's topic-based analysis is the primary boundary mechanism, but it only fires on incoming messages. The inactivity timeout catches the "user walked away" case. The threshold should be configurable per-deployment.

### DLT-005: Load foundational context for personality and user knowledge
**Status**: ✓ Implementation
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: Provide the assistant with foundational, always-available context through core files that are loaded with higher priority than dynamically retrieved memories. Three files establish the assistant's identity and baseline knowledge: SOUL.md defines personality traits, tone, and behavioral guidelines; USER.md captures known information about the user (name, preferences, projects, communication style); AGENTS.md provides operational instructions for the agent and sub-agents. These files ensure the assistant behaves consistently regardless of which memories are retrieved for a given conversation, and give the user a transparent, editable way to shape the assistant's behavior.

### DLT-006: Pre-process messages with memory context injection
**Status**: ✗ Defined
**Depends on**: DLT-008
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: Before the coordinator processes a user message, automatically gather and inject relevant context so responses are informed by past interactions. This delta delivers two things: a reusable, pluggable pre-processing pipeline that runs context providers in parallel before the agent sees a message, and the first provider — a memory context provider that searches stored memories using semantic similarity to find context relevant to the current message. The pipeline architecture must support adding more providers later (e.g., calendar, email, notes) without modifying the core pipeline. Retrieved memories are injected into the coordinator's context, enabling the assistant to reference past conversations, known preferences, and prior decisions naturally.

### DLT-008: Extract and store memories from conversations
**Status**: ⧗ Design
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: The complete memory write path — from conversation analysis to persistent storage. After a conversation ends (detected by DLT-004), automatically analyze the full exchange to extract what the assistant should remember. Each memory type has its own post-processor that forks the original SDK session and asks the agent — via a tailored prompt — to read the relevant memory subdirectory, then create, update, or delete memory files as needed. Three memory types for v1: episodic (date-stamped summaries of what happened, rewritten over time), facts (named files about the user — job, projects, family — updated when new info emerges), and preferences (named files about how the user likes things done — updated or deleted when preferences change). All processors run in parallel since they operate on independent subdirectories. Using markdown keeps memories human-readable and directly inspectable by the user, avoiding database dependencies for v1. This delta delivers two things: (1) a reusable, pluggable post-processing pipeline that runs processors in parallel after conversation end is detected (supporting additional post-processors later without modifying the core pipeline), and (2) the initial set of memory processors — one per memory type, each with its own extraction prompt and session fork. When DLT-020 is implemented, memory file writes should trigger automatic git commits to maintain workspace version history.

### DLT-009: Search memories by semantic similarity
**Status**: ✗ Defined
**Depends on**: DLT-008
**Priority**: 5 (Backlog)
**Complexity**: Hard
**Description**: Provide the ability to search stored memories by semantic similarity to a query, enabling the assistant to find relevant past context even when exact keywords don't match. Results are ranked by a combination of semantic relevance and time-based weighting (recent memories rank higher). This is the retrieval engine consumed by the memory context provider (DLT-006) and potentially other components that need to find relevant past context. The delta involves selecting and integrating an embedding model, building and maintaining an index over stored memories, and implementing the search/ranking logic. The embedding model choice should be evaluated during speccing, balancing quality, speed, and self-hosted requirements.

### DLT-010: Queue and process background tasks during idle time
**Status**: ✗ Defined
**Depends on**: DLT-003
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Enable the assistant to work proactively by creating, scheduling, and executing background tasks. This delta covers the full task lifecycle across two concerns: (1) **Task creation and storage** — a specialized sub-agent (delegated by the coordinator via DLT-003) handles task creation. It interprets user intent, extracts timing information, and structures task entries (e.g., "remind me about X tomorrow morning", "follow up on topic Y in a few hours", "summarize today's notes"). Tasks are stored in a persistent queue that survives restarts and support both immediate execution (process during next idle period) and time-based scheduling (process at or after a specified time). (2) **Task execution** — when no conversation is active (after DLT-004 detects conversation end), the system picks up eligible tasks — those whose scheduled time has passed or that have no time constraint — and executes them one at a time without interrupting the user. Results are stored and delivered at the start of the user's next interaction.

### DLT-011: Run as a persistent background service
**Status**: ✗ Defined
**Depends on**: DLT-024
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Run the assistant as a persistent background process that starts automatically on system boot and restarts on failure. This delta covers service lifecycle and process management only — it ensures the application is always running and recovers from crashes. Specific reconnection logic (Telegram) and state persistence (memory files) are handled by their respective deltas. Implementation should use standard Linux service management (e.g., systemd) appropriate for a single-user, self-hosted deployment.

### DLT-013: Add structured logging for agent actions
**Status**: ✓ Spec
**Depends on**: DLT-012
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Instrument the assistant with structured logging via loguru so that key agent actions — startup, message processing, coordinator lifecycle, and errors — are recorded in a consistent, machine-parseable format. Log entries include timestamps, log level, component context, and relevant metadata via keyword arguments. Logs are written to a file in the workspace data directory, configured through a bootstrap hook (DLT-023). Log level is configurable via the TOML config file (DLT-012). Follows conventions established in ADR-006 and DES-002.

### DLT-014: Add LLM observability for agent interactions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Track LLM calls across the entire system — the coordinator and all sub-agents — to provide visibility into how the underlying model is being used. Capture inputs (prompts/context sent), outputs (responses received), token usage, latency, and estimated costs per call. This enables understanding of which operations are expensive, identifying prompt quality issues, and optimizing token budgets over time. Local/self-hosted tooling is preferred over cloud analytics services; the specific solution should be evaluated during speccing to find the best fit for a single-user, privacy-conscious deployment.

### DLT-015: Set up evaluation framework for agent pipelines
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Medium
**Description**: Establish the foundation for testing agent processing pipelines with reproducible, automated test cases. The framework should support defining input scenarios (e.g., a conversation transcript, a user message with known relevant memories), running them through specific pipelines (pre-processing, post-processing), and comparing outputs against expected results using configurable assertions. This enables quality assurance for LLM-powered pipelines without relying on manual testing, and provides a regression safety net as pipelines evolve. The framework should be runnable locally and produce clear pass/fail reports.

### DLT-016: Eval: Context processing quality
**Status**: ✗ Defined
**Depends on**: DLT-006, DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the pre-processing pipeline using the evaluation framework (DLT-015). Tests whether the right memories and context are being retrieved and injected for given input messages. Test cases should cover: retrieving relevant memories when they exist, not injecting irrelevant context, handling messages where no relevant memories exist, and prioritizing recent/important memories appropriately. Measures precision (no irrelevant context injected) and recall (relevant context not missed) of the context injection process.

### DLT-017: Eval: Memory extraction quality
**Status**: ✗ Defined
**Depends on**: DLT-008, DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the post-processing pipeline using the evaluation framework (DLT-015). Tests whether the right facts, preferences, decisions, and patterns are being captured from sample conversations. Test cases should cover: extracting explicit facts, detecting implicit preferences, correctly categorizing memory types, avoiding hallucinated memories (extracting things that weren't actually discussed), and handling conversations with no extractable learnings. Measures completeness (nothing important missed), accuracy (correct categorization), and precision (no false extractions).

### DLT-018: Update core context files from conversation learnings
**Status**: ✗ Defined
**Depends on**: DLT-005, DLT-008
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: A dedicated post-processing processor (plugging into DLT-007's pipeline) that analyzes completed conversations for information that should update the assistant's foundational context files. Detects changes to user information (new job, moved cities, changed preferences) for USER.md, personality adjustments based on user feedback for SOUL.md, and operational instruction updates for AGENTS.md. Different from memory extraction — this updates long-lived foundational documents rather than creating individual memory entries. Must be conservative: only update when there's clear evidence, since these files carry higher weight than individual memories. When DLT-020 is implemented, core context file updates should trigger automatic git commits, making changes easy to review and roll back.

### DLT-019: Eval: Core context update quality
**Status**: ✗ Defined
**Depends on**: DLT-015, DLT-018
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the core context update post-processor using the evaluation framework (DLT-015). Tests whether the right updates are being applied to SOUL.md, USER.md, and AGENTS.md from sample conversations. Test cases should cover: detecting explicit user information changes, ignoring ambiguous or uncertain information, not overwriting correct existing information with noise, correctly routing updates to the right file (user info to USER.md, personality feedback to SOUL.md), and handling conversations with no context-file-relevant information. Measures precision (no false updates applied) and conservatism (only high-confidence changes are made).

### DLT-020: Git module for workspace version tracking
**Status**: ✓ Spec
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: A git module that hooks into the workspace initialization process (DLT-023) to set up the workspace directory as a git repository and provides tools for version tracking. The module initializes the repo during first-run setup (git init, sensible .gitignore), and exposes a commit utility that other components call after writing files to maintain automatic version history. Every write operation that modifies workspace files results in an automatic commit with a descriptive message, providing built-in history and the ability to roll back to any prior state. Includes startup validation that the workspace is a healthy git repo. The git integration is intentionally simple for v1 — linear history on a single branch, no merging, no PRs. Advanced workspace management (branching, two-tier change model, conflict resolution) is deferred to post-v1.

### DLT-021: Skill system with detection and pre-processing injection
**Status**: ✗ Defined
**Depends on**: DLT-006
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Deliver the complete v1 skill system: skills are markdown documents that define workflows or knowledge any agent (coordinator or sub-agents) can load when needed. Each skill document follows a standard format with metadata (name, description, trigger patterns) and content (instructions, steps, reference knowledge). A skill registry manages available skills — listing, looking up by name, and providing metadata for detection. Skills live in a dedicated directory within the workspace. A skills context provider plugs into the pre-processing pipeline (DLT-006) to automatically detect which skills are relevant to an incoming message and inject them into the agent's context. The provider queries the registry, matches skills against the current message using metadata and trigger patterns, and loads matched skill documents into the enriched context alongside memory. Detection should balance precision (don't load irrelevant skills that waste context) with recall (don't miss applicable skills). This delta does NOT cover constrained execution (post-v1).

### DLT-022: Eval: Skill detection quality
**Status**: ✗ Defined
**Depends on**: DLT-015, DLT-021
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the skills context provider using the evaluation framework (DLT-015). Tests whether the right skills are being detected and injected for given input messages. Test cases should cover: detecting relevant skills when they exist, not injecting irrelevant skills that waste context, handling messages where no skills apply, prioritizing when multiple skills match, and correctly loading skill content into agent context. Measures precision (no irrelevant skills injected) and recall (applicable skills not missed) of the skill detection process.

### DLT-024: Package and install agent as a uv tool
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Package the agent as an installable CLI tool using uv, enabling easy installation and updates via `uv tool install`. This delta covers project packaging configuration (pyproject.toml entry points, dependencies), a CLI entry point that starts the agent, and documentation for installation. The CLI entry point is the main way users launch the agent — it wires up the agent architecture (DLT-001), loads configuration (DLT-012), and starts the main loop. Using uv tool provides isolated dependency management and simple update path (`uv tool upgrade`).

### DLT-026: Detect conversation boundaries via topic analysis
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Add a step that runs before the pre-processing pipeline to actively detect whether an incoming message continues the current conversation or starts a new one. On each message, a lightweight agent compares the message content against the current session's topic and recent context. If it's a continuation, processing proceeds normally into the pre-processing pipeline. If it detects a topic shift or unrelated message, the system signals the session registry (DLT-027) to close the current session — triggering any post-processing on the completed conversation — and opens a new session before the coordinator sees the message. This is architecturally separate from the context-enrichment pipeline (DLT-006) — it's a lifecycle gating step, not a context provider. DLT-004's inactivity timeout remains as a fallback for detecting abandoned conversations (user goes silent without topic change). Should add no more than 1-2 seconds to message processing.


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
**Status**: ✓ Implementation
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Integrate with the Telegram Bot API to provide the primary production-facing communication channel for v1. Users send text messages to a Telegram bot, which forwards them to the coordinator agent. The coordinator's responses are streamed back via progressive message edits with correct markdown formatting, splitting at paragraph boundaries before hitting the Telegram message size limit. Tool activity is displayed as inline status lines within the response message. This delta covers the full Telegram lifecycle: bot initialization with token validation, receiving incoming messages via polling (with queuing for messages arriving during an active response), forwarding them into the agent architecture, streaming responses back, single-user authorization (silently ignoring unauthorized senders), connection resilience with automatic reconnection and backoff, and graceful shutdown (delivering partial responses on SIGTERM/SIGINT). Message validation silently ignores empty messages and non-text content. Telegram configuration (bot token, authorized chat ID) is stored in a TOML `[telegram]` section. Also introduces a CLI entry point with a `--channel` flag to select between the REPL (default) and Telegram, with CLI flags overriding TOML config values. Coordinator errors (recoverable and non-recoverable) are surfaced as messages in the Telegram chat.

### DLT-003: Delegate tasks to focused sub-agents
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Enable the coordinator to delegate specialized requests to focused sub-agents instead of handling everything itself. When a request requires specific expertise or tooling, the coordinator spawns a sub-agent that receives only the context and tools relevant to its task — preventing context poisoning and keeping each agent sharp. The sub-agent executes, returns its result, and the coordinator synthesizes it into a user-facing response. This delta covers the delegation mechanism itself (how to spawn, scope, and collect results from sub-agents); specific sub-agent types are defined by their own deltas. Error handling for sub-agent failures (timeouts, crashes, bad output) should be addressed as part of this mechanism.

### DLT-004: Detect conversation boundaries via inactivity timeout
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Fallback conversation boundary detection that monitors for periods of user inactivity. After a configurable threshold (~20 minutes by default), the system signals the session registry (DLT-027) to close the current session, triggering downstream post-processing. This serves as a safety net for cases where the user goes silent without a clear topic change — DLT-026's topic-based analysis is the primary boundary mechanism, but it only fires on incoming messages. The inactivity timeout catches the "user walked away" case. The threshold should be configurable per-deployment.

### DLT-006: Pre-process messages with memory context injection
**Status**: ⧗ Spec
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: Before the coordinator processes a user message, automatically gather and inject relevant context so responses are informed by past interactions. This delta delivers two things: a reusable, pluggable pre-processing pipeline that runs context providers in parallel before the agent sees a message, and the first provider — a memory context provider that searches stored memories using semantic similarity to find context relevant to the current message. The pipeline architecture must support adding more providers later (e.g., calendar, email, notes) without modifying the core pipeline. Retrieved memories are injected into the coordinator's context, enabling the assistant to reference past conversations, known preferences, and prior decisions naturally.

### DLT-009: Search memories by semantic similarity
**Status**: ✗ Defined
**Depends on**: None
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
**Depends on**: DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the post-processing pipeline using the evaluation framework (DLT-015). Tests whether the right facts, preferences, decisions, and patterns are being captured from sample conversations. Test cases should cover: extracting explicit facts, detecting implicit preferences, correctly categorizing memory types, avoiding hallucinated memories (extracting things that weren't actually discussed), and handling conversations with no extractable learnings. Measures completeness (nothing important missed), accuracy (correct categorization), and precision (no false extractions).

### DLT-018: Update core context files from conversation learnings
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: A dedicated post-processing processor (plugging into DLT-007's pipeline) that analyzes completed conversations for information that should update the assistant's foundational context files. Detects changes to user information (new job, moved cities, changed preferences) for USER.md, personality adjustments based on user feedback for SOUL.md, and operational instruction updates for AGENTS.md. Different from memory extraction — this updates long-lived foundational documents rather than creating individual memory entries. Must be conservative: only update when there's clear evidence, since these files carry higher weight than individual memories. When DLT-020 is implemented, core context file updates should trigger automatic git commits, making changes easy to review and roll back.

### DLT-019: Eval: Core context update quality
**Status**: ✗ Defined
**Depends on**: DLT-015, DLT-018
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the core context update post-processor using the evaluation framework (DLT-015). Tests whether the right updates are being applied to SOUL.md, USER.md, and AGENTS.md from sample conversations. Test cases should cover: detecting explicit user information changes, ignoring ambiguous or uncertain information, not overwriting correct existing information with noise, correctly routing updates to the right file (user info to USER.md, personality feedback to SOUL.md), and handling conversations with no context-file-relevant information. Measures precision (no false updates applied) and conservatism (only high-confidence changes are made).

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
**Status**: ✓ Design
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Add a step that runs before the pre-processing pipeline to actively detect whether an incoming message continues the current conversation or starts a new one. On each message, a lightweight agent compares the message content against the current session's topic and recent context. If it's a continuation, processing proceeds normally into the pre-processing pipeline. If it detects a topic shift or unrelated message, the system signals the session registry (DLT-027) to close the current session — triggering any post-processing on the completed conversation — and opens a new session before the coordinator sees the message. This is architecturally separate from the context-enrichment pipeline (DLT-006) — it's a lifecycle gating step, not a context provider. DLT-004's inactivity timeout remains as a fallback for detecting abandoned conversations (user goes silent without topic change). Should add no more than 1-2 seconds to message processing.


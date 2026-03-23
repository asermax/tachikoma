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

### DLT-009: Search memories by semantic similarity
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Hard
**Description**: Provide the ability to search stored memories by semantic similarity to a query, enabling the assistant to find relevant past context even when exact keywords don't match. Results are ranked by a combination of semantic relevance and time-based weighting (recent memories rank higher). This is the retrieval engine consumed by the memory context provider and potentially other components that need to find relevant past context. The delta involves selecting and integrating an embedding model, building and maintaining an index over stored memories, and implementing the search/ranking logic. The embedding model choice should be evaluated during speccing, balancing quality, speed, and self-hosted requirements.

### DLT-011: Run as a persistent background service
**Status**: ✗ Defined
**Depends on**: DLT-024
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Run the assistant as a persistent background process that starts automatically on system boot and restarts on failure. This delta covers service lifecycle and process management only — it ensures the application is always running and recovers from crashes. Specific reconnection logic (Telegram) and state persistence (memory files) are handled by their respective deltas. Implementation should use standard Linux service management (e.g., systemd) appropriate for a single-user, self-hosted deployment.

### DLT-014: Add LLM observability for agent interactions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Track LLM calls across the entire system — the coordinator and all sub-agents — to provide visibility into how the underlying model is being used. Capture inputs (prompts/context sent), outputs (responses received), token usage, latency, and estimated costs per call. This enables understanding of which operations are expensive, identifying prompt quality issues, and optimizing token budgets over time. Local/self-hosted tooling is preferred over cloud analytics services; the specific solution should be evaluated during speccing to find the best fit for a single-user, privacy-conscious deployment.

### DLT-015: Set up evaluation framework for agent pipelines
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Establish the foundation for testing agent processing pipelines with reproducible, automated test cases. The framework should support defining input scenarios (e.g., a conversation transcript, a user message with known relevant memories), running them through specific pipelines (pre-processing, post-processing), and comparing outputs against expected results using configurable assertions. This enables quality assurance for LLM-powered pipelines without relying on manual testing, and provides a regression safety net as pipelines evolve. The framework should be runnable locally and produce clear pass/fail reports.

### DLT-016: Eval: Context processing quality
**Status**: ✗ Defined
**Depends on**: DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the pre-processing pipeline using the evaluation framework (DLT-015). Tests whether the right memories and context are being retrieved and injected for given input messages. Test cases should cover: retrieving relevant memories when they exist, not injecting irrelevant context, handling messages where no relevant memories exist, and prioritizing recent/important memories appropriately. Measures precision (no irrelevant context injected) and recall (relevant context not missed) of the context injection process.

### DLT-017: Eval: Memory extraction quality
**Status**: ✗ Defined
**Depends on**: DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the post-processing pipeline using the evaluation framework (DLT-015). Tests whether the right facts, preferences, decisions, and patterns are being captured from sample conversations. Test cases should cover: extracting explicit facts, detecting implicit preferences, correctly categorizing memory types, avoiding hallucinated memories (extracting things that weren't actually discussed), and handling conversations with no extractable learnings. Measures completeness (nothing important missed), accuracy (correct categorization), and precision (no false extractions).

### DLT-019: Eval: Core context update quality
**Status**: ✗ Defined
**Depends on**: DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the core context update post-processor using the evaluation framework (DLT-015). Tests whether the right updates are being applied to SOUL.md, USER.md, and AGENTS.md from sample conversations. Test cases should cover: detecting explicit user information changes, ignoring ambiguous or uncertain information, not overwriting correct existing information with noise, correctly routing updates to the right file (user info to USER.md, personality feedback to SOUL.md), and handling conversations with no context-file-relevant information. Measures precision (no false updates applied) and conservatism (only high-confidence changes are made).

### DLT-022: Eval: Skill detection quality
**Status**: ✗ Defined
**Depends on**: DLT-015
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Build an eval suite for the skills context provider using the evaluation framework (DLT-015). Tests whether the right skills are being detected and injected for given input messages. Test cases should cover: detecting relevant skills when they exist, not injecting irrelevant skills that waste context, handling messages where no skills apply, prioritizing when multiple skills match, and correctly loading skill content into agent context. Measures precision (no irrelevant skills injected) and recall (applicable skills not missed) of the skill detection process.

### DLT-024: Package and install agent as a uv tool
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Package the agent as an installable CLI tool using uv, enabling easy installation and updates via `uv tool install`. This delta covers project packaging configuration (pyproject.toml entry points, dependencies), a CLI entry point that starts the agent, and documentation for installation. The CLI entry point is the main way users launch the agent — it wires up the agent architecture, loads configuration, and starts the main loop. Using uv tool provides isolated dependency management and simple update path (`uv tool upgrade`).

### DLT-031: Granular processing status messages
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Replace the single hardcoded "Thinking..." status message with granular, component-driven status updates during pre-processing and post-processing. Each pipeline component (context providers, post-processors, boundary detection) reports what it is currently doing via a status callback, and the coordinator forwards these as Status events to the active channel. This gives users real-time visibility into what the assistant is doing behind the scenes (e.g., "Searching memories...", "Detecting topic shift...", "Extracting memories...") instead of a generic indicator.

### DLT-032: Guide the assistant through skill authoring
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: Developers extending the system need the assistant to understand how to scaffold new skills correctly. Provide a built-in skill that activates when a user asks to create, define, or set up a new skill, injecting the full context the assistant needs — directory conventions, available capabilities, detection tuning guidance, and prompt-writing best practices — so it can produce well-structured skills without external documentation.

### DLT-033: Validate skill quality during authoring
**Status**: ✗ Defined
**Depends on**: DLT-032, DLT-015
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: During skill authoring, the assistant needs to verify that a new skill works correctly before finalizing it. Provide an interactive validation tool the assistant invokes mid-authoring to check two aspects: (1) detection quality — running the skill's description against synthetic messages via the evaluation framework to measure whether it triggers on relevant messages and avoids false matches, reporting precision/recall scores and actionable feedback; and (2) structural correctness — verifying required frontmatter fields, description completeness, agent definition validity, and adherence to skill conventions. Results include suggestions so the assistant can iteratively refine the skill until it passes quality thresholds, closing the authoring feedback loop without manual testing. This is distinct from the offline skill detection eval suite, which evolves the detection engine itself — this tool evolves individual skill descriptions to work well with the detection logic.

### DLT-034: Summarize agent actions instead of generic tool markers
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Replace the generic "Ran tools" marker in Telegram responses with a concise summary of what the agent actually did (e.g., "Read 3 files and searched for logging config" instead of "🔧 Ran tools"). The summary is generated from the sequence of ToolActivity events captured during the response, condensed into a human-readable action description that helps users understand what happened without reading tool-by-tool output.

### DLT-035: Receive images and audio from Telegram
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Accept image and audio messages from the Telegram channel and forward them to the agent for processing. Currently the Telegram handler only accepts text messages and silently ignores all other content types. This delta adds support for photos, voice messages, and audio files, forwarding them to the agent as multimodal input for processing.

### DLT-036: Auto-close idle sessions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: Automatically close a session after a configurable period of inactivity so that post-processing (memory extraction, context updates, git commit) triggers without requiring the user to explicitly end the conversation or wait for a topic shift. The idle timeout is measured from the last message exchange and is configurable via the application settings. This complements boundary-detection-based session closing by handling the case where a conversation simply trails off.

### DLT-037: Deliver notifications during active streaming
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Enable task notifications and scheduled alerts to reach the user even while the agent is actively streaming a response in Telegram. Currently, notifications arriving during an active stream may be blocked or delayed because the renderer is editing the response message. This delta ensures notifications are delivered within a reasonable window regardless of streaming state, without disrupting the active response rendering.

### DLT-038: Hot-reload skills at runtime
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: Reload the skill registry when skills are added or modified at runtime, without requiring an application restart. Currently the skill registry loads once during bootstrap and never refreshes. Since the agent can create and modify skill files during execution, newly authored or updated skills are invisible until the next restart. This delta adds a mechanism to detect skill changes and re-index skills so they become available immediately.

### DLT-039: Extract shared base for pipeline execution
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: The three pipelines (pre-processing, post-processing, and per-message post-processing) each independently implement the same parallel-with-isolation execution and error-gathering pattern, creating a maintenance risk when the pattern needs to change. Extract that shared orchestration logic into a common base so each pipeline becomes a thin specialization rather than a separate implementation with duplicated logic.

### DLT-040: Extract prompt-driven processor and fork-and-consume primitives
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: The prompt-driven processor pattern and the fork-and-consume helper are currently embedded in the post-processing module, but they represent general-purpose primitives used by any session-forking processor. Move them into standalone reusable modules so future processors can adopt them without depending on the post-processing pipeline.

### DLT-041: Persist session context to database
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Session context is currently held only in memory, making it unavailable for inspection, debugging, or future tooling that needs to know what the agent was told. This delta introduces a `SessionContext` model associated with `Session` to persist all context injected into a session — both foundational context (soul, user knowledge, agent guidelines) assembled at session start and first-message-dependent context (memories, projects, skills) gathered during pre-processing. Each entry carries an owner identifier for traceability and an injection order for deterministic assembly. The coordinator saves context entries when a new session is created and loads them back when resuming or inspecting a session, replacing ephemeral coordinator state with a queryable, persistent record of what context the agent was given for each session.

### DLT-042: Add invalidation and refresh support to persisted context entries
**Status**: ✗ Defined
**Depends on**: DLT-041
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: Persisted context entries can go stale when underlying data changes, but the coordinator has no mechanism to detect or respond to this. This delta adds an invalidation flag to the persisted context entry model and a pre-message check in the coordinator: before processing each message, any flagged entries are regenerated from their source and updated in the store. Completion criteria: the flag can be set externally and the coordinator regenerates the entry on the next message without requiring a restart.

### DLT-043: Move foundational context assembly into pre-processing pipeline with file-change invalidation
**Status**: ✗ Defined
**Depends on**: DLT-042
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Foundational context (soul, user knowledge, agent guidelines) is currently assembled once at startup, meaning changes to SOUL.md, USER.md, or AGENTS.md are not reflected until the process restarts. This delta moves that assembly into a dedicated pre-processing context provider and marks the corresponding context entries invalid whenever those files are written, so the next message automatically regenerates them from current file contents using the context invalidation and refresh infrastructure.

### DLT-044: Invalidate memories context on memory file changes
**Status**: ✗ Defined
**Depends on**: DLT-043
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Using the context invalidation mechanism, mark the memories context entry as invalid whenever a file under memories/ is written during the session. The next message triggers a fresh memory search against the updated memory store, ensuring the agent is not working with stale memory context after post-processing has extracted new memories from the conversation.

### DLT-045: Invalidate skills context on skill file changes
**Status**: ✗ Defined
**Depends on**: DLT-043
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Using the context invalidation mechanism, mark the skills context entry as invalid whenever a skill file under skills/ is written during the session. The next message triggers a fresh skill classification pass against the updated skill registry, complementing the runtime skill registry hot-reload by also refreshing the injected skills context so the agent immediately sees newly authored or modified skill instructions.

### DLT-046: Invalidate projects context on submodule changes
**Status**: ✗ Defined
**Depends on**: DLT-043
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Using the context invalidation mechanism, mark the projects context entry as invalid whenever a project submodule under projects/ changes state, detected by watching for writes to git ref files within the submodule directories. The next message triggers a fresh projects listing, ensuring the agent reflects the current state of registered projects without requiring a session restart.

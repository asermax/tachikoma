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

### DLT-004: Detect conversation boundaries via inactivity timeout
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Fallback conversation boundary detection that monitors for periods of user inactivity. After a configurable threshold (~20 minutes by default), the system signals the session registry to close the current session, triggering downstream post-processing. This serves as a safety net for cases where the user goes silent without a clear topic change — the topic-based boundary detector is the primary boundary mechanism, but it only fires on incoming messages. The inactivity timeout catches the "user walked away" case. The threshold should be configurable per-deployment.

### DLT-009: Search memories by semantic similarity
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Hard
**Description**: Provide the ability to search stored memories by semantic similarity to a query, enabling the assistant to find relevant past context even when exact keywords don't match. Results are ranked by a combination of semantic relevance and time-based weighting (recent memories rank higher). This is the retrieval engine consumed by the memory context provider and potentially other components that need to find relevant past context. The delta involves selecting and integrating an embedding model, building and maintaining an index over stored memories, and implementing the search/ranking logic. The embedding model choice should be evaluated during speccing, balancing quality, speed, and self-hosted requirements.

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
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Package the agent as an installable CLI tool using uv, enabling easy installation and updates via `uv tool install`. This delta covers project packaging configuration (pyproject.toml entry points, dependencies), a CLI entry point that starts the agent, and documentation for installation. The CLI entry point is the main way users launch the agent — it wires up the agent architecture, loads configuration, and starts the main loop. Using uv tool provides isolated dependency management and simple update path (`uv tool upgrade`).

### DLT-030: Manage external project repositories
**Status**: ✓ Reconciled
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Enable the assistant to manage external code repositories alongside its workspace as git submodules within a dedicated directory. On startup, all registered projects are pulled to their latest state. The coordinator can register new projects by name and git URL during conversations. At the end of each session, changes in project repositories are committed and pushed in parallel before the main workspace commit, reusing the same commit generation approach, ensuring submodule references stay in sync. This supports workflows where the user asks the assistant to track and contribute to multiple codebases.

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
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: Run the assistant as a persistent background process that starts automatically on system boot and restarts on failure. This delta covers service lifecycle and process management only — it ensures the application is always running and recovers from crashes. Specific reconnection logic (Telegram) and state persistence (memory files) are handled by their respective deltas. Implementation should use standard Linux service management (e.g., systemd) appropriate for a single-user, self-hosted deployment.

### DLT-014: Add LLM observability for agent interactions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Track LLM calls across the entire system — the coordinator and all sub-agents — to provide visibility into how the underlying model is being used. Capture inputs (prompts/context sent), outputs (responses received), token usage, latency, and estimated costs per call. This enables understanding of which operations are expensive, identifying prompt quality issues, and optimizing token budgets over time. Local/self-hosted tooling is preferred over cloud analytics services; the specific solution should be evaluated during speccing to find the best fit for a single-user, privacy-conscious deployment. Explore Laminar (https://laminar.sh/blog/2025-12-03-claude-agent-sdk-instrumentation) as a potential solution — it provides OpenTelemetry-based instrumentation specifically designed for Claude Agent SDK.

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

### DLT-033: Validate skill detection quality during authoring
**Status**: ✗ Defined
**Depends on**: DLT-032, DLT-015, DLT-054
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: During skill authoring, the assistant needs to verify that a new skill's description triggers correctly on relevant messages. Provide a validation tool that runs the skill's description against synthetic messages via the evaluation framework to measure whether it triggers on relevant messages and avoids false matches, reporting precision/recall scores and actionable feedback. Results include suggestions so the assistant can iteratively refine the skill's description until it passes quality thresholds, closing the authoring feedback loop without manual testing. The tool is exposed on the skill authoring guide skill via the skill-provided MCP tools capability. This is distinct from the offline skill detection eval suite, which evolves the detection engine itself — this tool evolves individual skill descriptions to work well with the existing detection logic.

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

### DLT-037: Ensure Telegram push notifications for streamed responses
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The Telegram channel streams responses by creating a single message and progressively editing it. Telegram only delivers push notifications for new messages, not edits — so if the user sends a message and closes the app before the response starts, they never receive a push notification that the agent replied. This delta ensures users receive a Telegram push notification for every agent response, even when the response is delivered via progressive message editing. The specific mechanism (e.g., sending a brief new message after streaming completes, or restructuring how the first message is created) should be evaluated during speccing.

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

### DLT-047: Proactive session handoff before context compaction
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When a conversation grows long enough that the SDK's auto-compaction would compress away injected context (memories, skills, foundational files), proactively detect context pressure and perform an explicit handoff — close the current session with a structured summary and open a new one with fresh context injection plus the summary as bridging context. This replaces opaque auto-compaction with a controlled transition that guarantees critical context survives. The detection mechanism (token estimation, message count heuristic, or SDK signal) and the summary format should be evaluated during speccing. The handoff reuses the existing session close/reopen infrastructure and bridging context assembly.

### DLT-048: Plugin system with install, discovery, and loading
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Hard
**Description**: Introduce a directory-based plugin system that allows extending the assistant with additional capabilities. A plugin is a self-describing directory with a manifest file that declares its structure and contributions — context providers, post-processors, skills, channels, or MCP tools. Plugins are installed by copying from a source location (local path or remote repository) into a managed plugins directory within the workspace. At startup, the plugin loader discovers installed plugins, validates their manifests, and feeds declared contributions into the existing registration points (bootstrap, pipelines, coordinator). Plugin loading is fail-safe: a broken plugin is logged and skipped without affecting the rest of the system. Plugin-specific configuration is managed through the existing TOML config under a plugins section.

### DLT-055: Plugin update mechanism
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 5 (Backlog)
**Complexity**: Medium
**Description**: Add an update mechanism to the plugin system that checks whether installed plugins' sources have newer versions available and synchronizes the local copy. Updates can be triggered explicitly by the user or run automatically at startup. The synchronization strategy depends on the source type (re-copy for local paths, pull for remote repositories). Failed updates should leave the existing plugin intact rather than corrupting it. This enables plugin authors to publish improvements and bug fixes that users can pull in without manually reinstalling.

### DLT-056: Plugin removal
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Add a removal mechanism to the plugin system that cleanly uninstalls a plugin by removing its directory from the managed plugins location and cleaning up any related configuration. This completes the plugin lifecycle by allowing users to discard plugins they no longer need.

### DLT-049: Plugin hook for custom context providers
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Allow plugins to contribute context providers that participate in the pre-processing pipeline. Plugin-declared providers implement the existing ContextProvider interface and are registered into the PreProcessingPipeline alongside built-in providers during plugin loading. This enables plugins to inject custom context (e.g., calendar events, external knowledge bases, CRM data) into every agent conversation without modifying the core system.

### DLT-050: Plugin hook for custom post-processors
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Allow plugins to contribute post-processors that participate in the post-processing pipeline. Plugin-declared processors implement the existing PostProcessor interface (or extend PromptDrivenProcessor) and are registered into the PostProcessingPipeline at a plugin-specified phase (main, pre_finalize, finalize) during plugin loading. This enables plugins to perform custom extraction, side effects, or integrations after a session closes (e.g., syncing extracted action items to a task tracker, sending conversation summaries to a webhook).

### DLT-051: Plugin hook for bundled skills
**Status**: ✗ Defined
**Depends on**: DLT-048, DLT-054
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Allow plugins to bundle pre-defined skills (with their agent definitions and MCP tool servers) that become available in the skill registry alongside user-authored skills. During plugin loading, each plugin's declared skill directories are added to the skill registry's search paths, making their skills discoverable by the skills context provider. This includes skills that provide MCP tool servers, which requires the skill-provided MCP tools capability to be in place. This enables plugins to ship ready-to-use capabilities (e.g., a "code review" plugin that includes a skill with specialized agents, prompts, and tools) without requiring users to manually copy skill files into the workspace.

### DLT-052: Concurrent secondary channels
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Medium
**Description**: Support running multiple communication channels concurrently instead of the current single-channel-per-run model. A user designates one primary channel for interactive conversations (REPL or Telegram as today) while additional secondary channels run alongside it, each able to receive and respond to messages through the same assistant. This enables scenarios like receiving proactive notifications through Telegram while working interactively via the REPL, or running plugin-contributed channels alongside built-in ones. Secondary channels follow the same interface as primary channels but are distinguished from the primary so the system can route responses and notifications correctly.

### DLT-053: Plugin hook for secondary channels
**Status**: ✗ Defined
**Depends on**: DLT-048, DLT-052
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Allow plugins to contribute secondary channels that run alongside the primary channel. Plugin-declared channels implement the same channel interface used by the built-in REPL and Telegram channels and are launched as secondary channels during startup using the concurrent channel infrastructure. This enables plugins to add new communication surfaces (e.g., a Slack channel, a web API, a Matrix bridge) without modifying the core application.

### DLT-054: Skill-provided MCP tools
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Medium
**Description**: Allow skills to expose MCP tool servers that become available to the main agent when the skill is activated. Currently skills can provide delegated agents but cannot give the main agent direct access to custom tools. This delta extends the skill definition format to declare MCP tool servers (either inline tool definitions or references to tool server scripts), and the skills context provider includes them in the ContextResult's mcp_servers field when the skill matches. This enables skills to provide interactive capabilities the agent can invoke directly (e.g., a "calendar" skill that provides tools to check availability and create events) rather than only through delegated agents.

### DLT-057: Validate skill structure and metadata
**Status**: ✗ Defined
**Depends on**: DLT-032, DLT-054
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: Skill authoring requires that new skills conform to the system's directory conventions and metadata contracts, but violations are only caught at runtime when the registry silently skips invalid entries. Provide a validation tool that checks a skill's structural correctness: SKILL.md exists with a valid description, agent definition files in agents/ have required frontmatter fields (description) and valid optional fields (model literals, tools as string lists), and the directory layout follows expected patterns. Results include actionable diagnostics listing each violation so the assistant can fix issues before finalizing a new skill. The tool is exposed on the skill authoring guide skill via the skill-provided MCP tools capability.

### DLT-058: Manual session close command
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Easy
**Description**: Provide a user-facing command to explicitly close the current session, triggering all post-processing (memory extraction, context updates, git commit) without requiring a topic shift or application restart. Currently sessions only close via boundary detection or graceful shutdown, leaving no way for the user to signal "I'm done with this topic." The command is invoked through the active channel (e.g., `/close` in Telegram, a REPL command) and delegates to the coordinator's existing session close logic. This also serves as the fallback mechanism when automatic boundary detection is disabled via configuration.

### DLT-059: Disable optional subsystems via configuration
**Status**: ✗ Defined
**Depends on**: DLT-058
**Priority**: 5 (Backlog)
**Complexity**: Medium
**Description**: Some users may not need all of Tachikoma's capabilities active — whether to simplify behavior, reduce resource usage, or tailor the assistant to a specific workflow. This delta adds per-feature enabled/disabled toggles to the application configuration, covering memory, session boundary detection, and projects. When a subsystem is disabled, all of its behavior is cleanly removed: it does not initialize at startup, does not contribute context or post-processing, and does not influence conversation flow. Disabling boundary detection means the manual session close command becomes the only way to trigger session post-processing mid-conversation. Toggles live in a `[features]` configuration section with boolean flags that default to enabled, preserving current behavior for users who do not customize.

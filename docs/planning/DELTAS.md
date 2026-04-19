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
**Depends on**: None
**Priority**: 3 (Medium)
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

### DLT-031: Granular processing status messages
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Replace the single hardcoded "Thinking..." status message with granular, component-driven status updates during pre-processing and post-processing. Each pipeline component (context providers, post-processors, boundary detection) reports what it is currently doing via a status callback, and the coordinator forwards these as Status events to the active channel. This gives users real-time visibility into what the assistant is doing behind the scenes (e.g., "Searching memories...", "Detecting topic shift...", "Extracting memories...") instead of a generic indicator.

### DLT-033: Validate skill detection quality during authoring
**Status**: ✗ Defined
**Depends on**: DLT-015, DLT-054
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: During skill authoring, the assistant needs to verify that a new skill's description triggers correctly on relevant messages. Provide a validation tool that runs the skill's description against synthetic messages via the evaluation framework to measure whether it triggers on relevant messages and avoids false matches, reporting precision/recall scores and actionable feedback. Results include suggestions so the assistant can iteratively refine the skill's description until it passes quality thresholds, closing the authoring feedback loop without manual testing. The tool is exposed on the skill authoring guide skill via the skill-provided MCP tools capability. This is distinct from the offline skill detection eval suite, which evolves the detection engine itself — this tool evolves individual skill descriptions to work well with the existing detection logic.

### DLT-039: Extract shared base for pipeline execution
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: The three pipelines (pre-processing, post-processing, and per-message post-processing) each independently implement the same parallel-with-isolation execution and error-gathering pattern, creating a maintenance risk when the pattern needs to change. Extract that shared orchestration logic into a common base so each pipeline becomes a thin specialization rather than a separate implementation with duplicated logic.

### DLT-040: Unify sub-agent execution into shared abstraction
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: The prompt-driven processor pattern, fork-and-consume helper, and ad-hoc SDK call construction are repeated across multiple sub-agent sites (post-processors, boundary detection, memory search, skills classification, task execution) with similar boilerplate for building options, calling the SDK, and consuming results. Extract a common agent execution abstraction — a class with shared methods for running sub-agents — that encapsulates these patterns, and refactor existing call sites to use it. This replaces scattered SDK option assembly and result consumption with a uniform interface, reducing duplication and making it easier to apply cross-cutting changes (like sandboxing or observability) to all sub-agents.

### DLT-042: Add invalidation and refresh support to persisted context entries
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Persisted context entries can go stale when underlying data changes, but the coordinator has no mechanism to detect or respond to this. This delta adds an invalidation flag to the persisted context entry model and a pre-message check in the coordinator: before processing each message, any flagged entries are regenerated from their source and updated in the store. Completion criteria: the flag can be set externally and the coordinator regenerates the entry on the next message without requiring a restart.

### DLT-043: Move foundational context assembly into pre-processing pipeline with file-change invalidation
**Status**: ✗ Defined
**Depends on**: DLT-042
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Foundational context (soul, user knowledge, agent guidelines) is currently assembled once at startup, meaning changes to SOUL.md, USER.md, or AGENTS.md are not reflected until the process restarts. This delta moves that assembly into a dedicated pre-processing context provider and marks the corresponding context entries invalid whenever those files are written, so the next message automatically regenerates them from current file contents using the context invalidation and refresh infrastructure.

### DLT-044: Invalidate memories context on memory file changes
**Status**: ✗ Defined
**Depends on**: DLT-043
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Using the context invalidation mechanism, mark the memories context entry as invalid whenever a file under memories/ is written during the session. The next message triggers a fresh memory search against the updated memory store, ensuring the agent is not working with stale memory context after post-processing has extracted new memories from the conversation.

### DLT-045: Invalidate skills context on skill file changes
**Status**: ✗ Defined
**Depends on**: DLT-043
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Using the context invalidation mechanism, mark the skills context entry as invalid whenever a skill file under skills/ is written during the session. The next message triggers a fresh skill classification pass against the updated skill registry, complementing the runtime skill registry hot-reload by also refreshing the injected skills context so the agent immediately sees newly authored or modified skill instructions.

### DLT-046: Invalidate projects context on submodule changes
**Status**: ✗ Defined
**Depends on**: DLT-043
**Priority**: 4 (Low)
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
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Introduce a directory-based plugin system that allows extending the assistant with additional capabilities. Where possible the plugin format reuses Claude Code's conventions (skill directories with SKILL.md, slash commands, MCP tool declarations) so existing Claude Code community plugins can be installed into Tachikoma without modification, unlocking the upstream ecosystem for free. On top of that base, Tachikoma adds its own contribution points that CC does not cover — context providers, post-processors, secondary channels, boundary-detection hooks, and any other extension surfaces the system exposes — so plugins can participate in the full pipeline, not just the parts CC knows about. The evaluation of which CC conventions to adopt as-is versus extend is part of the delta's scope. Plugins are installed by copying from a source location (local path or remote repository) into a managed plugins directory within the workspace. At startup, the plugin loader discovers installed plugins, validates their declarations, and feeds declared contributions into the existing registration points (bootstrap, pipelines, coordinator). Plugin loading is fail-safe: a broken plugin is logged and skipped without affecting the rest of the system. Plugin-specific configuration is managed through the existing TOML config under a plugins section.

### DLT-055: Plugin update mechanism
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Add an update mechanism to the plugin system that checks whether installed plugins' sources have newer versions available and synchronizes the local copy. Updates can be triggered explicitly by the user or run automatically at startup. The synchronization strategy depends on the source type (re-copy for local paths, pull for remote repositories). Failed updates should leave the existing plugin intact rather than corrupting it. This enables plugin authors to publish improvements and bug fixes that users can pull in without manually reinstalling.

### DLT-056: Plugin removal
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Add a removal mechanism to the plugin system that cleanly uninstalls a plugin by removing its directory from the managed plugins location and cleaning up any related configuration. This completes the plugin lifecycle by allowing users to discard plugins they no longer need.

### DLT-049: Plugin hook for custom context providers
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Allow plugins to contribute context providers that participate in the pre-processing pipeline. Plugin-declared providers implement the existing ContextProvider interface and are registered into the PreProcessingPipeline alongside built-in providers during plugin loading. This enables plugins to inject custom context (e.g., calendar events, external knowledge bases, CRM data) into every agent conversation without modifying the core system.

### DLT-050: Plugin hook for custom post-processors
**Status**: ✗ Defined
**Depends on**: DLT-048
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Allow plugins to contribute post-processors that participate in the post-processing pipeline. Plugin-declared processors implement the existing PostProcessor interface (or extend PromptDrivenProcessor) and are registered into the PostProcessingPipeline at a plugin-specified phase (main, pre_finalize, finalize) during plugin loading. This enables plugins to perform custom extraction, side effects, or integrations after a session closes (e.g., syncing extracted action items to a task tracker, sending conversation summaries to a webhook).

### DLT-051: Plugin hook for bundled skills
**Status**: ✗ Defined
**Depends on**: DLT-048, DLT-054
**Priority**: 4 (Low)
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
**Priority**: 4 (Low)
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
**Depends on**: DLT-054
**Priority**: 5 (Backlog)
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

### DLT-060: Check for agent updates and notify user
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Users running the agent need to stay current with bug fixes and features without manually checking for releases. This delta periodically checks for newer versions, notifies the user through their active channel when an update is available, and captures their choice (confirm, defer, or skip). Configuration includes how often checks occur. The apply action is handled by a separate delta.

### DLT-061: Apply agent update
**Status**: ✗ Defined
**Depends on**: DLT-060
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Apply a confirmed agent update using uv's upgrade mechanism. This delta executes when the user has confirmed an update via the notification from the update check delta, performing the actual update and optionally restarting the agent to run the new version.

### DLT-062: Restrict agent file writes to workspace directory
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Currently all agents run with `bypassPermissions` and no path restrictions, meaning they can modify any file the process has OS-level access to. Confine file writes, edits, and shell commands to the workspace path while preserving read access for broader system context. All SDK agent instances must be subject to the sandbox boundary, regardless of how they are created. The specific sandboxing mechanism (SDK-level configuration, permission mode restrictions, or another approach) should be evaluated during speccing.

### DLT-064: Collapse intensive work sections in Telegram
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When the agent performs intensive work — rapid sequences of tool calls interspersed with short text responses (e.g. reading, editing, and searching files during code implementation) — the Telegram channel currently renders every tool summary and intermediate text inline, producing long, noisy messages that obscure the final answer. This delta adds detection of intensive work patterns within the Telegram renderer: when the number of tool-to-text boundaries within a single Telegram message exceeds a configurable threshold, subsequent intermediate content (tool summaries and short bridging text) is wrapped in a collapsible section, leaving only the final substantive text visible by default. Detection resets at each Telegram message boundary (when the message splits due to length). The collapsing mechanism (Telegram's ExpandableBlockQuote, spoiler tags, or another approach) and the threshold tuning should be evaluated during speccing. Collapsible sections must only collapse after the tool execution is complete — in-progress tools should remain visible (expanded) so the user can see active work, transitioning to collapsed only when the next text or tool boundary confirms the tool has finished.

### DLT-065: Parallel conversation sessions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Hard
**Description**: Users can have multiple independent conversations with the assistant running simultaneously. When a new message arrives while the assistant is busy and represents a distinct topic, it spawns as a separate concurrent session with its own context and history. This enables users to follow up on something urgent without waiting for a long-running task to complete.

### DLT-066: Recover interrupted post-processing on restart
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When the process stops during session post-processing — whether from a crash, signal, or unhandled error — the work done by completed processors is preserved but remaining processors never run, leaving memory extraction, context updates, or git commits incomplete. This delta adds checkpoint tracking to the post-processing pipeline: each processor's completion is recorded as it finishes, and on startup the recovery hook detects sessions with incomplete post-processing and resumes from the last checkpoint, running only the processors that haven't completed yet. This prevents both data loss (skipped processors) and duplication (re-running completed ones).

### DLT-067: Telegram inline button support
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Enable the agent to present interactive inline buttons in Telegram conversations, allowing users to respond to structured prompts by tapping a button instead of typing. How buttons are triggered, rendered, and how user interactions are routed back to the agent should be evaluated during speccing.

### DLT-068: Structured error handling for message generation
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Currently, errors during session management, boundary detection, context loading, and metadata updates within the coordinator's message generation flow are silently logged, while only SDK stream errors surface to the user. Introduce error classification (severity levels, recoverability) and a surfacing mechanism that replaces silent logging and raw exception text with categorized messages indicating what went wrong and whether the conversation can continue normally. The classification scheme and surfacing approach established here become the standard adopted by subsequent error handling deltas.

### DLT-069: Structured error handling for pre-processing pipeline
**Status**: ✗ Defined
**Depends on**: DLT-068
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Apply the error classification and surfacing mechanism to the pre-processing pipeline. Currently, context provider failures (memory search, skills detection, projects loading) are silently logged and skipped — the agent proceeds with degraded context and neither the user nor the coordinator knows what was lost. Surface provider failures as classified error notices so users are informed when context is incomplete, enabling them to judge response quality or retry.

### DLT-070: Structured error handling for post-processing pipeline
**Status**: ✗ Defined
**Depends on**: DLT-068
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Apply the error classification and surfacing mechanism to both the session-level and per-message post-processing pipelines. Currently, processor failures during memory extraction, facts capture, preferences detection, context updates, and summary generation are silently logged — users never know whether their conversations were properly processed and persisted. Surface processor failures as classified error notices so users are informed when post-processing is incomplete, making extraction gaps visible rather than silently losing conversation learnings.

### DLT-071: Structured error handling for task execution
**Status**: ✗ Defined
**Depends on**: DLT-068
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Apply the error classification and surfacing mechanism to the task execution subsystem. Currently, task pre-processing fallbacks, evaluator failures, and notification delivery issues are handled with ad-hoc logging and silent degradation. Classify and surface failures during task pre-processing, evaluation loops, post-processing, and notification generation consistently with the rest of the system.

### DLT-074: Rename skills subsystem to avoid Claude Code naming collision
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Claude Code uses "skills" internally for its plugin-provided slash-command capabilities, and Tachikoma also uses "skills" for its own sub-agent packages. When both systems share the same term, the agent conflates them — attempting to invoke a Tachikoma skill via the Claude Code Skill tool, or ignoring a Claude Code skill because it assumes it belongs to Tachikoma's registry. This leads to incorrect tool routing and missed capabilities. Rename Tachikoma's skill subsystem to a distinct term (e.g., "modules", "packages", or "capabilities") across the codebase, configuration, and internal references, and add internal disambiguation logic so the agent reliably distinguishes between the two systems without relying on external guidance files.

### DLT-077: Route settings requests to correct config system
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: Claude Code has its own configuration system (global, project, and local settings files) that is separate from Tachikoma's TOML-based config. The agent sometimes confuses the two when asked to "update settings," modifying Claude Code settings when the user meant Tachikoma settings or vice versa. Add internal disambiguation logic — through system prompt injection, configuration metadata, or routing rules in the coordinator — that routes settings requests to the correct config system based on what is being configured (e.g., task scheduling routes to Tachikoma MCP tools, permissions and hooks route to Claude Code settings).

### DLT-078: Session routing rollback on context mismatch
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: When a message gets routed to a resumed session via boundary detection, there is no mechanism to undo the routing if the session context does not actually match the user's intent. The conversation gets forced down the wrong path with no recovery. Add a verification step that forks the candidate session and evaluates whether the incoming message makes sense within its context before committing to the routing, catching mismatches early instead of requiring the user to manually correct the course.

### DLT-080: Self-healing skill system via post-conversation analysis
**Status**: ✗ Defined
**Depends on**: DLT-123
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Skills currently only improve when the user explicitly notices a gap and requests changes. Add a post-conversation processor that analyzes skill usage during the completed session — which skills were invoked, which failed or were misapplied, what workarounds the agent resorted to — and surfaces concrete edit suggestions to the user for improving skill definitions. For example: detecting that a workflow required manually chaining references that should be linked, that a CLI flag used in practice is missing from a skill's guidance, or that documented instructions diverged from actual usage patterns. Suggestions are presented for user review and approval, not applied automatically.

### DLT-082: CLI for querying internal state
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Operators managing a Tachikoma deployment (especially on a remote server) currently have no way to inspect internal state without starting a full agent conversation or running raw SQLite queries against the database. Add CLI subcommands to the Tachikoma entry point for querying internal state: list and inspect task definitions and execution history, view session history and summaries, check which context entries are loaded, and review skill registry status. These commands read directly from the database and print formatted output, enabling quick operational checks and debugging without requiring an active agent session.

### DLT-083: External command processor for remote management
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Hard
**Description**: When Tachikoma runs on a remote server, the user needs to manage it from their local machine without SSH-ing in and running CLI commands directly. Add a lightweight command listener that runs as a separate process alongside the main Tachikoma process, accepting management commands (pause/resume tasks, close sessions, reload config, query status) over a network interface. A companion client on the local machine connects to this listener, enabling remote administration without interrupting active conversations. The IPC mechanism and security model (authentication, encryption) should be evaluated during speccing.

### DLT-085: Tracked schema migration system
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Replace the current pragma-based migration checks with a tracked migration system that records applied migrations in a dedicated database table. On startup, the system queries already-applied migrations and only executes new ones in order, skipping already-completed migrations entirely. This eliminates redundant schema inspection on every startup and provides a clean, extensible mechanism for adding future schema changes without accumulating pragma checks.

### DLT-086: Manual session switching via Telegram reply
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Allow the user to switch to a specific previous session by replying to a Telegram message that was part of that session. Currently, messages are routed automatically via boundary detection with no user override. This delta adds message-to-session tracking (associating Telegram message IDs with the session they belong to), reply detection in the Telegram channel, and explicit session routing when a reply targets a past session. The user replies to any message from a previous conversation and the new message is routed to that session instead of following automatic routing logic. Edge cases include replying to a message with no associated session or a closed session that shouldn't be resumed.

### DLT-088: Scheduled memory store maintenance
**Status**: ✗ Defined
**Depends on**: DLT-147
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: A scheduled system task that periodically reviews and cleans up the memory store. The task runs on a cron schedule using the silent-maintenance task execution path, evaluating stored memories against maintenance criteria — such as staleness, redundancy, relevance decay, or excessive granularity in episodic entries — and consolidating, archiving, or removing entries that no longer provide value. The specific criteria and cleanup strategies should be investigated during speccing by analyzing real memory data for common patterns worth addressing.

### DLT-089: Abort tool execution on stop steering message
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: When the user sends a steering message with stop intent (e.g., "stop", "cancel") during an active generation, immediately abort any in-progress tool execution chain rather than waiting for the full chain to complete before the message takes effect. Currently, steering messages do halt generation across all channels, but the agent continues executing queued tool calls before processing the stop — resulting in a noticeable delay. This delta detects stop intent in incoming steering messages and triggers an immediate interrupt that cuts the tool chain short, similar to how Esc works in Claude Code.

### DLT-093: Add task instance history MCP tool
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: There is no MCP tool to query task execution history, so the agent cannot answer whether a task ran or why it failed. The `task_instances` table tracks every run (ID, definition ID, status, scheduled time, start/completion times, result, created at) but it is only accessible via raw SQLite queries. Add a `list_task_instances` tool that queries execution history for a given task definition, with optional filters for status and result count, enabling the agent to inspect past runs and diagnose failures without falling back to direct database access.

### DLT-094: Delegate work to autonomous long-running agents
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: Persistent, communicative agents that execute extended work autonomously — unlike background tasks which are fire-and-forget with a single prompt and evaluator loop, these agents maintain ongoing sessions, report progress, ask clarifying questions, and collaborate with the user over time. Think autonomous coworkers rather than one-off jobs. The user delegates a task ("research this topic thoroughly and report back", "refactor this module over the next hour, ask me if you get stuck") and the agent works independently while keeping the user informed and able to course-correct. The user can see intermediate progress, answer agent questions mid-execution, and control the agent's lifecycle (pause, resume, terminate) — all without blocking their main conversation for other interactions.

### DLT-095: Enrich task execution records with SDK session tracking and structured errors
**Status**: ✗ Defined
**Depends on**: DLT-071
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Developers need to debug failed background tasks and understand execution history, but task instances currently record only status, timestamps, and a free-text result — with no link to the SDK session that ran, no transcript reference, and no structured error context. This delta enriches the task instance model and execution flow with traceability data: recording the SDK session ID and transcript path for each background execution, capturing structured error context (error type, message, tool calls leading to failure) on failure using the error classification from the structured error handling subsystem, and computing execution duration as a first-class field. These fields enable querying past executions by session, inspecting failure artifacts, and displaying execution metrics without manual timestamp arithmetic. The scope is limited to the tasks subsystem — background jobs are not interactive conversations, but they still require an audit trail linking execution to its artifacts and outcomes.

### DLT-102: Prevent stale cron from firing on create/update
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: When a cron task is created or updated with a schedule time that has already passed today, the instance generator fires it immediately to "catch up" instead of waiting for the next scheduled occurrence. For example: a task runs at 4 PM, you update it to 8 AM at noon, it fires right away instead of waiting until tomorrow. Add a `since` timestamp field to `task_definitions` that gets set to `now()` on every create and update operation. The instance generator should only create instances for cron matches that fall after the `since` timestamp, ensuring schedule changes never trigger retroactive firings. This addresses a distinct problem from preventing duplicate instances within the same cron period — here the issue is newly-created or recently-updated tasks firing retroactively for past schedule matches.

### DLT-103: Defer session tasks until user idle instead of silently skipping
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Session-type cron tasks are gated by an idle check — they only fire when the user has been inactive for at least the configured idle window. When the user is active at the scheduled time, the session task scheduler silently skips the evaluation and waits for the next cron match, which may never arrive during continued activity (e.g., the Sunday planning routine can go weeks without firing if the user happens to be active every Sunday at the scheduled time). Instead of dropping evaluations, record that a session task became due and defer its firing until the idle window is eventually reached, so the task eventually runs rather than silently disappearing. The deferral record should include the original scheduled time and the reason for deferral so the user and agent can see pending session tasks via task status queries. A staleness policy (e.g., discard deferrals older than N hours, or collapse multiple missed firings into a single run) should be evaluated during speccing to prevent stale tasks from firing at unexpected times.

### DLT-104: Auto-cleanup of completed one-shot tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: One-shot tasks (reading list nudges, reminders) accumulate in the database after they fire and auto-disable — 14 accumulated in just two weeks of usage. The task system should automatically delete completed one-shot task definitions after a configurable retention period (default 24-48 hours). Only one-shot tasks whose instances have all reached terminal status (completed or failed) are eligible for cleanup. Recurring cron tasks are excluded from this cleanup regardless of instance status. The cleanup runs as part of the existing instance generator polling loop, checking for expired one-shot definitions on each evaluation cycle.

### DLT-105: Agent-driven episodic memory search
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Episodic memories (conversation summaries stored as date-stamped markdown files in `memories/episodic/`) are currently only accessible through the memory context provider, which runs at session start and injects a static set of memories. The agent has no way to search or retrieve episodic memories on demand during a conversation — it cannot answer "what did we discuss about X last month?" or proactively look up context when the topic shifts. Add MCP tools that let the agent search episodic memories by keyword, date range, or relevance during conversations. This complements the automatic injection (which handles initial context) by enabling the agent to pull in additional memories as the conversation evolves. The search mechanism should reuse the existing memory search strategy (Glob → Grep → Read) and return excerpts with date context.

### DLT-106: Proactive nudges from past conversations
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Hard
**Description**: The core proactive nudge capability: the agent identifies topics worth following up on from past conversations and delivers a nudge to the user, similar to how a friend might text "hey, been thinking about what you said about X..." Unlike scheduled tasks (which are reactive — defined at fixed times for known purposes) or reminders (which track explicit user requests), proactive nudges are agent-initiated. This delta covers the reflection pass over recent episodic memories to detect follow-up-worthy topics (open threads, time elapsed since discussion, topic resurfacing), the decision logic for when to nudge, and the delivery mechanism using the existing session task system. Starts with simple time-based frequency capping (e.g., max one nudge per day) as a built-in safeguard. Richer fatigue management and user preference controls are a separate delta.

### DLT-107: Sensor framework for proactive nudge signals
**Status**: ✗ Defined
**Depends on**: DLT-106
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: The engine that registers, runs, and aggregates scored signals from input sensors into the proactive nudge system. Provides the sensor abstraction (each sensor produces a data payload with a relevance score and optional nudge suggestion), a scheduling framework for running sensors on different cadences (polling vs event-driven), and user-facing configuration to enable/disable individual sensors and adjust sensitivity. The nudge engine evaluates all active sensor signals to decide whether to create a session task. This is the framework layer — individual sensors (memory, routine, calendar, etc.) are separate deltas that plug into this framework.

### DLT-108: Transcript search and analysis tools
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Provide CLI subcommands and MCP tools to search, filter, and analyze archived conversation transcripts stored in the project workspace. Enable the agent to reference past conversations by searching transcript content, and enable operators to review conversation history from the terminal. The initial scope covers full-text search across transcripts with date filtering and basic excerpt retrieval — enough for the agent to answer "what did we discuss about X?" by searching past transcripts. Summary extraction and conversation statistics are follow-up enhancements.

### DLT-109: Nudge fatigue management and user preferences
**Status**: ✗ Defined
**Depends on**: DLT-106
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Rich controls for managing proactive nudge frequency and relevance, building on the basic time-based capping provided by the core proactive nudges capability. Covers configurable frequency limits (per-day, per-week caps), relevance thresholds (minimum score before a nudge is delivered), user control over which topics the agent tracks for nudging purposes, and opt-out mechanisms. This ensures the nudge system remains helpful rather than annoying as the agent identifies more follow-up opportunities over time.

### DLT-110: Memory sensor for proactive nudge signals
**Status**: ✗ Defined
**Depends on**: DLT-107
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: The baseline sensor for the proactive nudge framework, using existing episodic memory data. Polls recent episodic memories with priority weighting for conversations that have open threads, upcoming events mentioned in past chats, or topics that have been discussed multiple times. Produces scored signals (data + relevance score + optional nudge suggestion) that feed into the nudge engine via the sensor framework. This is the first concrete sensor implementation and validates the sensor abstraction. Additional sensors (routine, calendar, time-based, geo-fencing, external events) follow the same pattern and are tracked separately.

### DLT-111: Prevent message loss during response finalization
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: When the user sends a message at the exact moment a previous response is finalizing, the new message can be silently dropped. The coordinator consumes the message from the queue but loses it during the transition between completing the previous turn's post-processing and picking up the next turn. This delta eliminates that timing window so that every consumed message is guaranteed to be processed, even when it arrives during response finalization.

### DLT-116: Provide workflow tools to background tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: Background tasks currently receive memory, projects, skills context, and a notification tool, but have no access to workflow management tools. This means a background task cannot start, advance, or complete a workflow — limiting scheduled automation to single-prompt fire-and-forget work. Attach the workflow tools MCP server to background task executions so the agent can operate on workflows during scheduled or autonomous runs.

### DLT-117: Attach specific skills to task definitions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Task definitions currently rely on LLM-based skill classification at execution time to determine which skills are relevant. This is unreliable for tasks that always need specific skills — the classifier might not select the right skills for a terse task prompt. Add an optional list of skill names to the task definition model. When a task executes, the named skills are loaded unconditionally into the context before the classification step runs, ensuring the agent always has the domain knowledge the task requires. The task creation and update MCP tools expose this field so the agent can attach skills when defining tasks.

### DLT-118: Declare dependencies between skills
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Skills are currently loaded independently — when the classifier selects a skill, only that skill's content is injected. Some skills implicitly depend on concepts or tools defined in other skills (e.g., a domain skill that assumes the workflow authoring guide is available). Add a `depends_on` field to the SKILL.md frontmatter that lists skill names this skill requires. When a skill is selected (by classification, task attachment, or workflow step binding), the registry resolves its dependency chain and ensures all required skills are loaded into context. Circular dependencies are detected and reported as warnings.

### DLT-119: Declare required skills for workflow steps
**Status**: ✗ Defined
**Depends on**: DLT-118
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: Workflow steps are self-contained instruction bundles with no mechanism to declare which skills the agent needs to execute them. A step that involves git operations, API calls, or domain-specific knowledge relies on the skill classifier to infer this from the step instructions — which may fail for terse or technical steps. Allow workflow step authors to explicitly declare the skills required for a step. When the workflow engine activates a step, the declared skills and their transitive dependencies (via the skill dependency system) are loaded unconditionally into the agent's context, bypassing classification.

### DLT-120: Pause background tasks to request user input
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: Background tasks currently run to completion or failure in a single uninterrupted evaluator loop — there is no way for a task to pause mid-execution and ask the user a question. This delta adds a "waiting" state to the task lifecycle where the evaluator can suspend execution and send a notification requesting user input. When the user responds, the input is injected as the next turn in the suspended task's SDK session and execution resumes. This enables simple multi-step background work (e.g., "research this and ask me before proceeding") without requiring persistent agent sessions, progress reporting, or full lifecycle control.

### DLT-121: Git-friendly database storage for workspace versioning
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: The SQLite database file is committed to the workspace git repository on every session close, but as a binary file it produces opaque diffs that bloat the git history over time — each commit stores a full copy of the database rather than meaningful deltas. Replace the current storage approach with one that produces human-readable, diffable representations of the database state so that changes are trackable in git history with the same fidelity as memory files and context documents. Approaches to evaluate during speccing include replacing SQLite with a git-native database like Dolt, exporting SQLite tables to diffable text formats (SQL dumps, sorted CSV/TSV via tools like sqlite-diffable) alongside or instead of the binary file, or other mechanisms that preserve queryable database access while making commits meaningful. The chosen approach must support the existing async query patterns used throughout the codebase.

### DLT-122: Evaluate alternatives to Claude Agent SDK
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: The project has moderate coupling to the Claude Agent SDK — the adapter layer and domain types are well-isolated, but the coordinator, post-processing forking, and background task executor are tightly bound to SDK-specific concepts (session resume, session forking, in-process MCP servers, custom transport). Evaluate alternative agent platforms (e.g., Anthropic's managed agents platform) to determine whether switching would provide meaningful benefits (managed infrastructure, richer session primitives, reduced operational burden) that justify the migration effort. The evaluation should assess feature parity against the SDK capabilities currently used (session resume, session forking, MCP server injection, multi-turn background execution, tool-less classification agents), estimate migration scope and risk, and produce a recommendation with a concrete migration path if switching is warranted. The deliverable is a decision document (ADR), not an implementation.

### DLT-123: Agent reflections memory type
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Add a memory category for the agent's own subjective assessments — what worked, what didn't, patterns it notices about its own behavior and capabilities. Distinct from existing memory types (episodic, facts, preferences) which capture objective user-centric information. Reflections are the agent's self-observations, forming the input layer for automated self-improvement. A new post-processing processor extracts reflections from completed sessions and stores them alongside other memory types. This enables the self-healing skill system and future self-learning capabilities to draw on the agent's accumulated experience rather than starting from scratch each session.

### DLT-125: Message envelope for typed message routing
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Introduce a message envelope — a wrapper around all messages entering the pipeline that declares the content type and carries type-specific metadata. Plain text messages get a thin wrapper; media messages carry file path, content type, dimensions, generated summaries, and other modality-specific data. The envelope gives preprocessors, boundary detection, context providers, and the agent a consistent way to inspect what kind of content they are handling without channel-specific logic. Each channel adapter wraps its messages in the same envelope format on ingestion, making the pipeline channel-agnostic. This is a foundational change that enables modality-aware processing throughout the system.

### DLT-126: Media preprocessor for content understanding
**Status**: ✗ Defined
**Depends on**: DLT-125
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Move content understanding into a dedicated preprocessor that generates a human-readable summary of incoming media files (e.g., "a screenshot of a USDC/ARS exchange rate showing 1 USDC = 1,463.85 ARS"). Currently, media arrives as metadata (file path + caption) with no actual content analysis. The preprocessor reads the media file from the message envelope's attachment metadata, dispatches modality-specific analysis (image description, audio transcription, document parsing), and appends the generated summary to the envelope. Every downstream component — boundary detection, context providers, the agent itself — then has a richer understanding of what the file contains without needing to analyze it independently.

### DLT-127: Immediate background task execution
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: Allow firing a background task immediately on demand instead of only through scheduled triggers. Currently tasks can only be created with a cron expression or a future one-shot datetime — there is no way to say "run this task right now." Add an MCP tool or extend existing tools to trigger immediate execution of a task definition, bypassing the scheduler and running the task's prompt through the background task executor directly. Works alongside background task pause/resume — e.g., pause a long-running task, make changes, then re-run it immediately without setting up a new scheduled trigger.

### DLT-128: Surface running detached processes in agent context
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: Currently running detached processes should be part of the agent's injected context so the agent is always aware of them and can monitor proactively without having to explicitly check. Add a context provider that queries the detached process registry on each message, surfaces active processes (name, PID, uptime, log path), and injects this information into the agent's pre-processing context. This enables the agent to proactively notice when a process has been running for an unusual duration, has stopped unexpectedly, or is relevant to the current conversation.

### DLT-129: Background pipeline runner
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 5 (Backlog)
**Complexity**: Hard
**Description**: A system for defining and executing repeatable multi-step pipelines as a single background process. A pipeline is an ordered sequence of heterogeneous steps — shell commands, background task prompts, notifications, agent handoffs — where each step's output flows as input to the next. Pipelines can be triggered by the main session, a long-running agent, or a scheduled task. This complements the existing background task system (single-prompt fire-and-forget) and autonomous agent delegation (interactive, long-lived sessions) by covering the middle ground: deterministic, sequential workflows that chain different execution modes without requiring an interactive agent to orchestrate each step. Design should consider how pipelines interact with the target system for directing outputs to specific sessions or agents.

### DLT-130: Progress visibility for long-running background tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Heavy background tasks (reading 9+ files, web research, writing large outputs) can take 6+ hours to complete with no intermediate status. The user has no way to know if a task is still running, stuck, or dead. Add progress tracking to the background task executor: log periodic status updates (e.g., every N minutes or when the agent uses a tool) to a queryable location, expose running task status through the existing MCP tools and CLI, and enforce a configurable timeout threshold — if a task exceeds the limit, fail it with a notification instead of silently burning resources. The goal is that a user can always answer "is this task still running and what is it doing?" without requiring SSH or raw database access.

### DLT-133: Defer file delivery to post-response in Telegram
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Files sent via the Telegram `send_file` MCP tool are delivered immediately during agent execution, so the user sees the file before the response message (because the file sends while the response is still being composed). UX would be better if files arrived after the message, like a natural attachment. Defer file sends to a post-response hook that dispatches them after the agent's text response is fully delivered. Tradeoff: deferring means the agent cannot react to delivery failures (retry, notify) because the response is already finalized by the time the file goes out. Either accept this risk or design a post-response feedback mechanism for delivery errors.

### DLT-134: Extend background tasks at iteration limit
**Status**: ✗ Defined
**Depends on**: DLT-120
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Background tasks currently hard-fail when they reach their maximum iteration count, even if they are making meaningful progress. This delta allows the assistant to escalate to the user when the iteration limit is reached instead of failing outright. The user is presented with the task's progress and the assistant's latest assessment, and can choose to grant additional iterations or abort. If extended, the task continues from where it left off with a fresh iteration budget. If aborted, the task is failed as today. The iteration limit before escalation is configurable per task definition, falling back to the global default. This prevents premature failure of tasks that are progressing slowly but productively, giving the user control over the cost/completion tradeoff.

### DLT-135: Serialize concurrent notification delivery and user message processing
**Status**: ✗ Defined
**Depends on**: DLT-111
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: When a background task notification is delivered at the exact moment a user sends a message, the two can collide at the coordinator's message queue — resulting in one being lost or the notification being silently swallowed. The existing message-loss prevention and notification buffering mechanisms handle their respective timing windows independently, but do not cover the case where both sources attempt to enqueue simultaneously. This delta adds serialization between the notification delivery path and the user message intake so that concurrent arrivals are safely ordered and neither is dropped.

### DLT-136: Document `.tachikoma/config/` as the standard location for skill and script configuration
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: The `.tachikoma/config/` directory is the established location for storing configuration files consumed by skills and scripts, using a per-skill subdirectory pattern (`.tachikoma/config/<skill-name>/config.toml`). This convention is not documented anywhere, so new skills and scripts don't follow it consistently. Document the `.tachikoma/config/` directory as the standard configuration location, including the subdirectory pattern, loading conventions, and integration with the skill authoring guide.

### DLT-137: API rate limit detection and retry with backoff
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When the API returns 429 rate limit errors, the system has no code-level retry mechanism. The executor passes raw errors to the evaluator, which gets stuck saying "continue" until max iterations are hit. Add rate limit detection at the API call level with exponential backoff retry, applied uniformly across background task execution, pre/post processing sub-agents, and session tasks.

### DLT-138: Stagger parallel API-consuming pipeline operations
**Status**: ✗ Defined
**Depends on**: DLT-137
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Pre-processing and post-processing pipelines spawn multiple sub-agents concurrently via `asyncio.gather()`, creating burst API load that triggers rate limits even with retry logic in place. This delta adds configurable concurrency control between sub-agent spawns within these pipelines — for example, using a semaphore or staggered dispatch — to reduce burst API usage. This complements the reactive retry mechanism by preventing unnecessary rate limit hits and reducing total API cost. Specific sequencing strategies (e.g., whether memory search waits for skill classification) should be evaluated during speccing.

### DLT-141: Pause background tasks on user activity
**Status**: ✗ Defined
**Depends on**: DLT-120
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When a user message arrives at the coordinator during an active background task execution, signal the background task to pause so the main session receives full API attention and the task does not compete for resources. The paused task's SDK session is preserved and execution resumes automatically once the main session returns to idle. This covers the system-initiated pause triggered by user activity — distinct from task-initiated pauses where the background task itself requests user input. The pause mechanism integrates with the existing background task executor's evaluation loop: when a pause signal is received between iterations, the executor suspends the task, records the paused state in the task instance, and releases the semaphore slot. On resume, the executor reacquires a slot and continues from the preserved SDK session.


### DLT-145: Inject core context into skill classification
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The skills context provider classifies which skills are relevant to an incoming message via a lightweight sub-agent whose prompt currently sees only the unloaded skill descriptions and the user message. Inject the foundational context (SOUL.md, USER.md, AGENTS.md) — the agent's personality, user knowledge, and agent guidelines that already inform the main loop — into the classifier's prompt so relevance decisions benefit from the same nuance the main agent has (e.g., the user's domain expertise or agent conventions that make some skills obviously more applicable than others).

### DLT-146: Surface active skills and loaded memories to post-processors
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The post-processing memory extractors (episodic, facts, preferences) and the core context update processor fork the SDK session with prompts describing their extraction task, but never tell them which skills were active during the session or which memory entries were loaded into context. Without that awareness, processors re-extract facts that were already in memory (duplication) and miss that skill-provided instructions influenced agent behavior. Include a summary — names only, not full content — of the aggregate set of skills and memory entries loaded across the session's turns in each processor's prompt so extractions avoid duplicating captured information and processors understand the context in which the conversation happened.

### DLT-147: System task type for silent maintenance operations
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Task definitions currently support `session` (idle-gated) and `background` (notifies user on completion/failure) types, both assuming user-facing work. Maintenance operations — memory reconciliation, stale task cleanup, vault consistency checks, log rotation — don't warrant user-visible notifications but still need the same scheduling infrastructure. Add a third `system` task type that reuses the background execution path but suppresses all notification dispatch (success, failure, and explicit `send_notification` calls made by the task itself). System tasks remain discoverable through existing task history tools so operators can inspect their runs. Scheduling triggers can be cron-based or condition-driven (e.g., "run reconciliation when N sessions have accumulated"); the trigger mechanism should be evaluated during speccing.

### DLT-148: Sharpen scope boundaries across post-processor prompts
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: The post-processing extractors for episodic memory, facts, preferences, and core context updates each have their own prompt, but their outputs currently overlap in ways that don't serve distinct purposes. Episodic entries recap CLI commands instead of summarizing high-level arcs, facts paste full diffs or code snippets instead of capturing what-and-why, and preferences restate technical details already covered in facts. Refine each processor's prompt with sharper guidance on what belongs in its memory type versus others, including concrete positive and negative examples drawn from observed output. Intentional duplication across types stays valid when the framing differs (a topic appearing in both a fact and a preference with different angles), but incidental overlap should be eliminated. Scope boundaries to enforce: episodic stays high-level narrative, facts stay declarative what/why, preferences stay behavioral. Verification is a spot-review of extracted memories after the change to confirm overlap reduction without losing coverage.

### DLT-149: Sort tool usage list newest-first in agent context
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Easy
**Description**: The tool usage summary injected into the agent's pre-processing context currently lists invocations in chronological order (oldest first). The most recent tool usages are typically the most relevant signal for the current turn — they reflect what the agent was just doing and which tools are likely to be needed next. Reverse the ordering so the newest entries appear at the top of the list, improving the signal density of this slice of the agent's context.

### DLT-150: Output phase markers for agent work styling
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: The agent currently produces a single undifferentiated output stream that mixes internal work (file reads, searches, tool chains) with the final user-facing response. Channels have no way to distinguish these phases, so presentation is uniform — everything is equally visible, which adds noise in surfaces like Telegram where conversational flow matters more than process transparency. Introduce phase markers the agent emits to tag different kinds of work — at minimum research, execution, and response — propagated through the adapter as typed events. Channels consume the markers to style phases differently: collapse "boring work" sections, hide internal phases entirely, or suppress the final message when the whole turn was marked hidden (e.g., silent background-style activity). The canonical phase vocabulary, how the agent is nudged to emit markers, and the rendering rules per channel (starting with Telegram) are part of the delta's scope. The feature separates the agent's internal process from what the user sees and gives channels fine-grained control over message presentation without the agent having to format output differently per channel.

### DLT-151: Secure credential delivery to whitelisted commands
**Status**: ✗ Defined
**Depends on**: DLT-156
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: A mechanism where the agent can pipe passwords and credentials to specific pre-approved CLI commands without ever seeing the plaintext. The agent knows that a secret exists and which command needs it, but the actual value flows directly from a secrets store to the CLI's stdin or environment, never through the agent's context or output. This prevents credential leaks through conversation logs, tool outputs, or prompt injection. The whitelist is critical: only commands explicitly registered in configuration can receive secrets. The mechanism exposes a pluggable provider interface so different backends (1Password CLI SDK, Bitwarden CLI, local encrypted file) can be swapped via configuration. The delta delivers the injection mechanism, the whitelist configuration schema, the provider interface, and at least one concrete backend implementation. The encrypted token store provides the local backend that ships out of the box.

### DLT-152: Boundary-aware message queueing
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Medium
**Description**: Run boundary detection on each incoming message and either steer it into the active session or queue it for sequential processing instead of force-routing or dropping. Currently, messages arriving while the agent is busy are either force-routed to the active session or dropped. This delta adds a boundary-aware routing layer that detects when an incoming message belongs to a different topic than the active session and queues it for processing after the current session's turn completes. This is an intermediate step toward full parallel conversation sessions — it provides message-aware routing and sequential queueing without requiring concurrent session execution.

### DLT-153: Immediate steering mode for session tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Allow session tasks to fire immediately as steering messages into the active conversation, bypassing the idle wait. Currently, session tasks are gated by an idle check — they only fire when the user has been inactive for a configured period. This delta adds an optional immediate mode to session tasks that, when enabled, skips the idle check and injects the task's content as a steering message into the active conversation at the earliest opportunity. This enables reminder-like behavior where time-sensitive prompts reach the user without waiting for idle. The mode is configured per task definition and uses the existing steering message infrastructure.

### DLT-154: Role-based git permission guardrails
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Agents operating inside Tachikoma (main user-driven agent, background tasks, post-processors, sub-agents spawned by pre/post-processing) currently share a single git access surface with no role-based restrictions, so any of them can commit, push, or otherwise mutate repository state. This is risky for non-user-driven roles: a runaway background task or a misbehaving post-processor could publish unintended commits or corrupt remote history before the user sees what happened. Introduce a permission layer that scopes git access by agent role — at minimum, background tasks, post-processors, and forked sub-agents must be read-only on git (no `commit`, no `push`, no `git` subcommand that mutates the working tree or refs), while the main agent retains the full surface. The layer should live at the permission/deny-rule level so restrictions apply regardless of whether the agent reaches git through bash, an MCP tool, or the auto-commit system. The delta delivers the role classification, the deny-rule enforcement, and sensible defaults per role.

### DLT-155: Project push/sync MCP tools with git deny rules
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The agent currently reaches git through raw bash, which means it either has too much power (destructive operations like `git push --force`, `git reset --hard`, `git checkout .`, `git clean`, and `git remote` changes are all reachable) or too little guidance (no clear primitives for pushing and syncing workspace and project submodules). This delta narrows the surface: expose two dedicated MCP tools — `push` (push the current branch to its remote) and `sync` (pull then push) — both aware of workspace and project submodules, and simultaneously add bash deny rules for destructive git operations (`git push --force`, `git reset --hard`, `git checkout .`, `git clean`, `git remote` modifications). No commit tool is exposed; the existing auto-commit system remains the sole way to create commits. The net result is a small, safe git surface the agent can rely on while anything that could cause irrecoverable damage is locked down.

### DLT-156: Encrypted token store for repo-committed secrets
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Tokens and other sensitive values currently sit as plaintext files under `.tachikoma/config/`, which means they are readable by the agent and end up in any transcript, context dump, or accidental commit. Introduce a secrets store where values live encrypted inside the repository and are accessed programmatically without ever exposing plaintext to the agent. Requirements: secrets live in the repo (encrypted, git-friendly), the store exposes a CLI suitable for piping values to command stdin or environment variables, the agent never sees plaintext at any layer (context, logs, tool output), and secret rotation is straightforward. Approaches to evaluate during speccing include age-encryption (simple, file-based, no daemon), SOPS (encrypted YAML/JSON with readable git diffs), and git-crypt (transparent per-pattern encryption). This delta delivers the store itself — encryption scheme, repo layout, CLI, and the access path used by downstream consumers such as the secure credential delivery mechanism (DLT-151).


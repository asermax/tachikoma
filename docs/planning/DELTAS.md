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

### DLT-031: Granular processing status messages
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Replace the single hardcoded "Thinking..." status message with granular, component-driven status updates during pre-processing and post-processing. Each pipeline component (context providers, post-processors, boundary detection) reports what it is currently doing via a status callback, and the coordinator forwards these as Status events to the active channel. This gives users real-time visibility into what the assistant is doing behind the scenes (e.g., "Searching memories...", "Detecting topic shift...", "Extracting memories...") instead of a generic indicator.

### DLT-033: Validate skill detection quality during authoring
**Status**: ✗ Defined
**Depends on**: DLT-015, DLT-054
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: During skill authoring, the assistant needs to verify that a new skill's description triggers correctly on relevant messages. Provide a validation tool that runs the skill's description against synthetic messages via the evaluation framework to measure whether it triggers on relevant messages and avoids false matches, reporting precision/recall scores and actionable feedback. Results include suggestions so the assistant can iteratively refine the skill's description until it passes quality thresholds, closing the authoring feedback loop without manual testing. The tool is exposed on the skill authoring guide skill via the skill-provided MCP tools capability. This is distinct from the offline skill detection eval suite, which evolves the detection engine itself — this tool evolves individual skill descriptions to work well with the existing detection logic.

### DLT-035: Receive images and audio from Telegram
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Accept image and audio messages from the Telegram channel and forward them to the agent for processing. Currently the Telegram handler only accepts text messages and silently ignores all other content types. This delta adds support for photos, voice messages, and audio files, forwarding them to the agent as multimodal input for processing.

### DLT-039: Extract shared base for pipeline execution
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: The three pipelines (pre-processing, post-processing, and per-message post-processing) each independently implement the same parallel-with-isolation execution and error-gathering pattern, creating a maintenance risk when the pattern needs to change. Extract that shared orchestration logic into a common base so each pipeline becomes a thin specialization rather than a separate implementation with duplicated logic.

### DLT-040: Unify sub-agent execution into shared abstraction
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
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
**Depends on**: DLT-054
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
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Currently all agents run with `bypassPermissions` and no path restrictions, meaning they can modify any file the process has OS-level access to. Confine file writes, edits, and shell commands to the workspace path while preserving read access for broader system context. All SDK agent instances must be subject to the sandbox boundary, regardless of how they are created. The specific sandboxing mechanism (SDK-level configuration, permission mode restrictions, or another approach) should be evaluated during speccing.

### DLT-063: Send files and media to users
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When the agent creates or references files during a conversation — images, documents, audio, or other media — users currently have no way to receive them directly; all output flows as streamed text. This delta enables the agent to deliver files to users as part of the conversation, with each channel rendering them in the most appropriate way for its medium. The specific mechanism for detecting which files to surface and the supported media types should be evaluated during speccing.

### DLT-064: Collapse intensive work sections in Telegram
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When the agent performs intensive work — rapid sequences of tool calls interspersed with short text responses (e.g. reading, editing, and searching files during code implementation) — the Telegram channel currently renders every tool summary and intermediate text inline, producing long, noisy messages that obscure the final answer. This delta adds detection of intensive work patterns within the Telegram renderer: when the number of tool-to-text boundaries within a single Telegram message exceeds a configurable threshold, subsequent intermediate content (tool summaries and short bridging text) is wrapped in a collapsible section, leaving only the final substantive text visible by default. Detection resets at each Telegram message boundary (when the message splits due to length). The collapsing mechanism (Telegram's ExpandableBlockQuote, spoiler tags, or another approach) and the threshold tuning should be evaluated during speccing. Collapsible sections must only collapse after the tool execution is complete — in-progress tools should remain visible (expanded) so the user can see active work, transitioning to collapsed only when the next text or tool boundary confirms the tool has finished.

### DLT-065: Parallel conversation sessions
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
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
**Priority**: 3 (Medium)
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

### DLT-072: Fix task management MCP tool bugs
**Status**: ⧗ Plan
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The task management MCP tools have multiple bugs that force the agent to fall back to raw SQLite queries. `list_tasks` does not expose task IDs, making it impossible to discover which ID to pass to `update_task` or `delete_task`. `update_task` rejects valid inputs with an unhelpful generic validation error that does not indicate which field failed or what schema is expected, and it is missing the `task_type` parameter — the column exists in the database but cannot be set through the MCP tool, preventing task type changes without falling back to raw SQL. The `notify` parameter description is misleading: it says "if omitted, background tasks run silently" but the system actually notifies on failure by default, leading to redundant notify strings. Tool descriptions lack parameter type documentation and cross-references between tools, leading to trial-and-error usage. Fix all of these: expose IDs in list output, fix update validation, expose `task_type` in `update_task`, clarify `notify` default behavior, and enrich tool descriptions with types, examples, and usage guidance.

### DLT-073: Block Claude Code built-in cron tools in default config
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: Claude Code ships with built-in `CronCreate`, `CronDelete`, and `CronList` tools that create session-only in-memory cron jobs. These shadow Tachikoma's persistent task system — the agent defaults to the built-in tools since they appear first in the tool list, and any reminders created through them silently vanish on exit because they are never persisted to the database or picked up by Tachikoma's scheduler. A manual workaround exists (adding these tools to the deny list in `.claude/settings.local.json`), but this is not baked into the default project configuration. Incorporate the deny list into the project template or default configuration so every workspace starts with these tools blocked.

### DLT-074: Rename skills subsystem to avoid Claude Code naming collision
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Claude Code uses "skills" internally for its plugin-provided slash-command capabilities, and Tachikoma also uses "skills" for its own sub-agent packages. When both systems share the same term, the agent conflates them — attempting to invoke a Tachikoma skill via the Claude Code Skill tool, or ignoring a Claude Code skill because it assumes it belongs to Tachikoma's registry. This leads to incorrect tool routing and missed capabilities. Rename Tachikoma's skill subsystem to a distinct term (e.g., "modules", "packages", or "capabilities") across the codebase, configuration, and internal references, and add internal disambiguation logic so the agent reliably distinguishes between the two systems without relying on external guidance files.

### DLT-075: Re-evaluate skill context per message
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: The skills context provider currently runs only on the first message of a new session — its output is persisted and reused for all subsequent messages in that session. When a conversation shifts topic mid-session (e.g., the user starts discussing routines after talking about a reading list), newly relevant skills are never loaded because the classification was based on the first message alone. Re-evaluate skill relevance on each message so follow-up messages can trigger loading of additional skills that match the evolving conversation context.

### DLT-076: Re-evaluate memory context per message
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: The memory context provider currently runs only on the first message of a new session — its output is persisted and reused for all subsequent messages in that session. When the conversation topic evolves, the initially retrieved memories may no longer be the most relevant, and memories that would be highly relevant to follow-up messages are never injected. Re-evaluate memory relevance on each message so the agent always has the most pertinent memories for the current point in the conversation.

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

### DLT-079: Escape markdown-sensitive characters in Telegram output
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: When the Telegram channel displays search commands or tool output containing glob or regex patterns with asterisks, the asterisks are interpreted as markdown formatting (italic/bold) instead of being rendered literally. For example, `* Searching for 'git.*push'` renders with broken formatting instead of displaying as plain text. Escape markdown-sensitive characters in displayed patterns and tool output so they render correctly in Telegram messages, and format tool activity output (file paths, commands, search patterns) using code blocks for improved readability.

### DLT-080: Self-healing skill system via post-conversation analysis
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Skills currently only improve when the user explicitly notices a gap and requests changes. Add a post-conversation processor that analyzes skill usage during the completed session — which skills were invoked, which failed or were misapplied, what workarounds the agent resorted to — and surfaces concrete edit suggestions to the user for improving skill definitions. For example: detecting that a workflow required manually chaining references that should be linked, that a CLI flag used in practice is missing from a skill's guidance, or that documented instructions diverged from actual usage patterns. Suggestions are presented for user review and approval, not applied automatically.

### DLT-081: Workflow state machine for skills
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Skills that define multi-step workflows (e.g., a morning routine skill that sequences reading a plan, having a conversation, marking activities, and updating a calendar) currently rely entirely on the LLM to remember which steps are done and what comes next. Without explicit state, the agent skips steps, repeats completed ones, or loses its place after context compaction. Introduce a workflow construct that lets skills declare ordered steps with completion conditions, tracks progression across messages, and injects step-specific reminders or continuations into the agent's context — enabling the agent to reliably execute multi-step workflows like deploying a service (build → test → push → verify) or processing a reading list (fetch → summarize → file → notify).

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

### DLT-084: Resume matching conversation on return after restart
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Easy
**Description**: When a user sends a message after a restart, the system always starts a new conversation even if the message relates to a recently discussed topic. This delta enables the system to check the incoming message against recent closed session summaries and resume the matching conversation instead of starting fresh, providing seamless topic continuation across process restarts.

### DLT-085: Tracked schema migration system
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Replace the current pragma-based migration checks with a tracked migration system that records applied migrations in a dedicated database table. On startup, the system queries already-applied migrations and only executes new ones in order, skipping already-completed migrations entirely. This eliminates redundant schema inspection on every startup and provides a clean, extensible mechanism for adding future schema changes without accumulating pragma checks.

### DLT-086: Manual session switching via Telegram reply
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Allow the user to switch to a specific previous session by replying to a Telegram message that was part of that session. Currently, messages are routed automatically via boundary detection with no user override. This delta adds message-to-session tracking (associating Telegram message IDs with the session they belong to), reply detection in the Telegram channel, and explicit session routing when a reply targets a past session. The user replies to any message from a previous conversation and the new message is routed to that session instead of following automatic routing logic. Edge cases include replying to a message with no associated session or a closed session that shouldn't be resumed.

### DLT-087: Disable Claude Code built-in skills in default config
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: Claude Code ships with a built-in `Skill` tool that provides access to plugin-provided slash-command capabilities. This shadows Tachikoma's own skill subsystem — the agent conflates the two systems, attempting to invoke Tachikoma skills via the Claude Code Skill tool or ignoring Claude Code skills assuming they belong to Tachikoma's registry. This delta disables the Skill tool through the default project configuration's deny list, alongside the cron tools already blocked by DLT-073, preventing the agent from accessing Claude Code's skill system entirely. This is the immediate mitigation while the full renaming solution (DLT-074) is deferred.

### DLT-088: Scheduled memory store maintenance
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: A scheduled background task that periodically reviews and cleans up the memory store. The task runs on a cron schedule using the background task execution system, evaluating stored memories against maintenance criteria — such as staleness, redundancy, relevance decay, or excessive granularity in episodic entries — and consolidating, archiving, or removing entries that no longer provide value. The specific criteria and cleanup strategies should be investigated during speccing by analyzing real memory data for common patterns worth addressing.

### DLT-089: Abort tool execution on stop steering message
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When the user sends a steering message with stop intent (e.g., "stop", "cancel") during an active generation, immediately abort any in-progress tool execution chain rather than waiting for the full chain to complete before the message takes effect. Currently, steering messages do halt generation across all channels, but the agent continues executing queued tool calls before processing the stop — resulting in a noticeable delay. This delta detects stop intent in incoming steering messages and triggers an immediate interrupt that cuts the tool chain short, similar to how Esc works in Claude Code.

### DLT-090: Prevent duplicate task instances from scheduler
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: The cron evaluator for session-type tasks fires multiple times within the same scheduled minute, creating duplicate instances. The `last_fired_at` field is updated after firing but does not prevent re-queuing within the same cron period — the scheduler only checks whether the task has ever fired, not whether it has already fired for the current period. This causes users to receive multiple duplicate notifications for a single scheduled execution. Deduplicate based on the current cron period rather than just the `last_fired_at` timestamp, ensuring each cron match produces at most one task instance.

### DLT-091: Conditional notification suppression for background tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Background tasks have no mechanism to conditionally suppress notifications at runtime. The `notify` field is static text set at task definition time, so every execution either always notifies or never does — there is no way for the task's output to signal whether notification is warranted. This is a problem for tasks that should only notify when there is meaningful content (e.g., a routine check-in that should stay silent when the daily plan is empty but notify when activities are scheduled). Add a mechanism for background tasks to signal at execution time whether the result should trigger a notification. The specific signaling approach should be evaluated during design — the key requirement is that the task's output can deterministically control whether the notification fires, without requiring the task to be redefined.

### DLT-092: Timezone-aware scheduling for one-shot tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 1 (Critical)
**Complexity**: Easy
**Description**: One-shot datetime schedules are interpreted as UTC without respecting the configured timezone, causing tasks created with local time to silently land in the past and fail with a confusing "must be in the future" error. The `_parse_schedule` function defaults to UTC when no timezone info is provided in the datetime string. Plumb the configured timezone into the task tool server context so bare datetimes are interpreted as local time, and document the timezone behavior in the `create_task` tool description with examples of accepted formats.

### DLT-093: Add task instance history MCP tool
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: There is no MCP tool to query task execution history, so the agent cannot answer whether a task ran or why it failed. The `task_instances` table tracks every run (ID, definition ID, status, scheduled time, start/completion times, result, created at) but it is only accessible via raw SQLite queries. Add a `list_task_instances` tool that queries execution history for a given task definition, with optional filters for status and result count, enabling the agent to inspect past runs and diagnose failures without falling back to direct database access.

### DLT-094: Delegate work to autonomous long-running agents
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Persistent, communicative agents that execute extended work autonomously — unlike background tasks which are fire-and-forget with a single prompt and evaluator loop, these agents maintain ongoing sessions, report progress, ask clarifying questions, and collaborate with the user over time. Think autonomous coworkers rather than one-off jobs. The user delegates a task ("research this topic thoroughly and report back", "refactor this module over the next hour, ask me if you get stuck") and the agent works independently while keeping the user informed and able to course-correct. The user can see intermediate progress, answer agent questions mid-execution, and control the agent's lifecycle (pause, resume, terminate) — all without blocking their main conversation for other interactions.

### DLT-095: Enrich task execution records with SDK session tracking and structured errors
**Status**: ✗ Defined
**Depends on**: DLT-090, DLT-071
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Developers need to debug failed background tasks and understand execution history, but task instances currently record only status, timestamps, and a free-text result — with no link to the SDK session that ran, no transcript reference, and no structured error context. This delta enriches the task instance model and execution flow with traceability data: recording the SDK session ID and transcript path for each background execution, capturing structured error context (error type, message, tool calls leading to failure) on failure using the error classification from the structured error handling subsystem, and computing execution duration as a first-class field. These fields enable querying past executions by session, inspecting failure artifacts, and displaying execution metrics without manual timestamp arithmetic. The scope is limited to the tasks subsystem — background jobs are not interactive conversations, but they still require an audit trail linking execution to its artifacts and outcomes.

### DLT-096: Include last exchange in session resumption candidates
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: The boundary detector evaluates whether an incoming message should resume a previous session, but it only receives the candidate sessions' summaries — which are rolling condensations of the full conversation. Including the actual last user message and assistant response from each candidate session would give the routing decision significantly better signal about whether the new message belongs in that session, especially for recent conversations where the summary may not yet capture the latest context.

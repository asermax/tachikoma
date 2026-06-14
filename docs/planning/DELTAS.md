# Delta Inventory

Deltas (work items) for Tachikoma on the pi stack. Completed deltas are removed once their behavior lands in the feature specs; this file only ever lists open work.

Each delta carries: **Status** (✗ Pending / ⧖ In Progress), **Note** (one-line behavioral description), **Depends on**, **Priority**, and **Complexity**.

An earlier roadmap (44 deltas) was completed and cleared on 2026-06-13; `git log docs/planning/DELTAS.md` preserves the history.

## Deltas

Deltas carried over from the long-term backlog; original numbering kept for traceability (it does not relate to the cleared roadmap numbering). Some descriptions reference older mechanics and get re-specced for the pi stack when picked up.

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
**Description**: Track LLM calls across the entire system — the coordinator and all sub-agents — to provide visibility into how the underlying model is being used. Capture inputs (prompts/context sent), outputs (responses received), token usage, latency, and estimated costs per call. This enables understanding of which operations are expensive, identifying prompt quality issues, and optimizing token budgets over time. Local/self-hosted tooling is preferred over cloud analytics services; the specific solution should be evaluated during speccing to find the best fit for a single-user, privacy-conscious deployment. Explore Laminar (https://laminar.sh/blog/2025-12-03-claude-agent-sdk-instrumentation) as a potential solution — it provides OpenTelemetry-based instrumentation for agent runtimes.

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

### DLT-047: Proactive session handoff before context compaction
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When a conversation grows long enough that auto-compaction would compress away injected context (memories, skills, foundational files), proactively detect context pressure and perform an explicit handoff — close the current session with a structured summary and open a new one with fresh context injection plus the summary as bridging context. This replaces opaque auto-compaction with a controlled transition that guarantees critical context survives. The detection mechanism (token estimation, message count heuristic, or SDK signal) and the summary format should be evaluated during speccing. The handoff reuses the existing session close/reopen infrastructure and bridging context assembly.

### DLT-052: Concurrent secondary channels
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Support running multiple communication channels concurrently instead of the current single-channel-per-run model. A user designates one primary channel for interactive conversations (REPL or Telegram as today) while additional secondary channels run alongside it, each able to receive and respond to messages through the same assistant. This enables scenarios like receiving proactive notifications through Telegram while working interactively via the REPL, or running plugin-contributed channels alongside built-in ones. Secondary channels follow the same interface as primary channels but are distinguished from the primary so the system can route responses and notifications correctly.

### DLT-062: Restrict agent file writes to workspace directory
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Currently all agents run with `bypassPermissions` and no path restrictions, meaning they can modify any file the process has OS-level access to. Confine file writes, edits, and shell commands to the workspace path while preserving read access for broader system context. All agent instances must be subject to the sandbox boundary, regardless of how they are created. The specific sandboxing mechanism (runtime-level configuration, permission mode restrictions, or another approach) should be evaluated during speccing.

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

### DLT-094: Delegate work to autonomous long-running agents
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: Persistent, communicative agents that execute extended work autonomously — unlike background tasks which are fire-and-forget with a single prompt and evaluator loop, these agents maintain ongoing sessions, report progress, ask clarifying questions, and collaborate with the user over time. Think autonomous coworkers rather than one-off jobs. The user delegates a task ("research this topic thoroughly and report back", "refactor this module over the next hour, ask me if you get stuck") and the agent works independently while keeping the user informed and able to course-correct. The user can see intermediate progress, answer agent questions mid-execution, and control the agent's lifecycle (pause, resume, terminate) — all without blocking their main conversation for other interactions.

### DLT-095: Enrich task execution records with agent session tracking and structured errors
**Status**: ✗ Defined
**Depends on**: DLT-071
**Priority**: 4 (Low)
**Complexity**: Medium
**Description**: Developers need to debug failed background tasks and understand execution history, but task instances currently record only status, timestamps, and a free-text result — with no link to the agent session that ran, no transcript reference, and no structured error context. This delta enriches the task instance model and execution flow with traceability data: recording the agent session ID and transcript path for each background execution, capturing structured error context (error type, message, tool calls leading to failure) on failure using the error classification from the structured error handling subsystem, and computing execution duration as a first-class field. These fields enable querying past executions by session, inspecting failure artifacts, and displaying execution metrics without manual timestamp arithmetic. The scope is limited to the tasks subsystem — background jobs are not interactive conversations, but they still require an audit trail linking execution to its artifacts and outcomes.

### DLT-105: Agent-driven episodic memory search
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Episodic memories (conversation summaries stored as date-stamped markdown files in `memories/episodic/`) are currently only accessible through the memory context provider, which runs at session start and injects a static set of memories. The agent has no way to search or retrieve episodic memories on demand during a conversation — it cannot answer "what did we discuss about X last month?" or proactively look up context when the topic shifts. Add agent tools that let the agent search episodic memories by keyword, date range, or relevance during conversations. This complements the automatic injection (which handles initial context) by enabling the agent to pull in additional memories as the conversation evolves. The search mechanism should reuse the existing memory search strategy (Glob → Grep → Read) and return excerpts with date context.

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
**Description**: Provide CLI subcommands and agent tools to search, filter, and analyze archived conversation transcripts stored in the project workspace. Enable the agent to reference past conversations by searching transcript content, and enable operators to review conversation history from the terminal. The initial scope covers full-text search across transcripts with date filtering and basic excerpt retrieval — enough for the agent to answer "what did we discuss about X?" by searching past transcripts. Summary extraction and conversation statistics are follow-up enhancements.

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

### DLT-123: Learnings memory layer
**Status**: ✗ Defined
**Depends on**: DLT-172
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Add a dedicated learnings layer to the memory model — a `learnings/` store of `<slug>.md` files capturing recurring friction, strict constraints, and repeated failures observed across sessions (e.g. an uncooperative test suite, a deployment step that keeps biting, a hard rule the user enforces), alongside the agent's own subjective self-observations about what worked and what didn't. This generalizes the earlier agent-reflections idea: reflections become one kind of learning. A post-processing extractor folds learnings from each completed session into the store, distinct from topic files (stable what/why knowledge about subjects) and episodic summaries (the narrative of what happened). Learnings are the experience substrate that later self-improvement work draws on, built on the topic-oriented memory store's layout, indexing, and extraction conventions.

### DLT-126: Media preprocessor for content understanding
**Status**: ✗ Defined
**Depends on**: DLT-125
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Move content understanding into a dedicated preprocessor that generates a human-readable summary of incoming media files (e.g., "a screenshot of a USDC/ARS exchange rate showing 1 USDC = 1,463.85 ARS"). Currently, media arrives as metadata (file path + caption) with no actual content analysis. The preprocessor reads the media file from the message envelope's attachment metadata, dispatches modality-specific analysis (image description, audio transcription, document parsing), and appends the generated summary to the envelope. Every downstream component — boundary detection, context providers, the agent itself — then has a richer understanding of what the file contains without needing to analyze it independently.

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
**Description**: Heavy background tasks (reading 9+ files, web research, writing large outputs) can take 6+ hours to complete with no intermediate status. The user has no way to know if a task is still running, stuck, or dead. Add progress tracking to the background task executor: log periodic status updates (e.g., every N minutes or when the agent uses a tool) to a queryable location, expose running task status through the existing agent tools and CLI, and enforce a configurable timeout threshold — if a task exceeds the limit, fail it with a notification instead of silently burning resources. The goal is that a user can always answer "is this task still running and what is it doing?" without requiring SSH or raw database access.

### DLT-133: Defer file delivery to post-response in Telegram
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Files sent via the Telegram `send_file` agent tool are delivered immediately during agent execution, so the user sees the file before the response message (because the file sends while the response is still being composed). UX would be better if files arrived after the message, like a natural attachment. Defer file sends to a post-response hook that dispatches them after the agent's text response is fully delivered. Tradeoff: deferring means the agent cannot react to delivery failures (retry, notify) because the response is already finalized by the time the file goes out. Either accept this risk or design a post-response feedback mechanism for delivery errors.

### DLT-134: Extend background tasks at iteration limit
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Background tasks currently hard-fail when they reach their maximum iteration count, even if they are making meaningful progress. This delta allows the assistant to escalate to the user when the iteration limit is reached instead of failing outright. The user is presented with the task's progress and the assistant's latest assessment, and can choose to grant additional iterations or abort. If extended, the task continues from where it left off with a fresh iteration budget. If aborted, the task is failed as today. The iteration limit before escalation is configurable per task definition, falling back to the global default. This prevents premature failure of tasks that are progressing slowly but productively, giving the user control over the cost/completion tradeoff.

### DLT-138: Stagger parallel API-consuming pipeline operations
**Status**: ✗ Defined
**Depends on**: DLT-137
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Pre-processing and post-processing pipelines spawn multiple sub-agents concurrently, creating burst API load that triggers rate limits even with retry logic in place. This delta adds configurable concurrency control between sub-agent spawns within these pipelines — for example, using a semaphore or staggered dispatch — to reduce burst API usage. This complements the reactive retry mechanism by preventing unnecessary rate limit hits and reducing total API cost. Specific sequencing strategies (e.g., whether memory search waits for skill classification) should be evaluated during speccing.

### DLT-141: Pause background tasks on user activity
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: When a user message arrives at the coordinator during an active background task execution, signal the background task to pause so the main session receives full API attention and the task does not compete for resources. The paused task's agent session is preserved and execution resumes automatically once the main session returns to idle. This covers the system-initiated pause triggered by user activity — distinct from task-initiated pauses where the background task itself requests user input. The pause mechanism integrates with the existing background task executor's evaluation loop: when a pause signal is received between iterations, the executor suspends the task, records the paused state in the task instance, and releases the semaphore slot. On resume, the executor reacquires a slot and continues from the preserved agent session.

### DLT-150: Output phase markers for agent work styling
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: The agent currently produces a single undifferentiated output stream that mixes internal work (file reads, searches, tool chains) with the final user-facing response. Channels have no way to distinguish these phases, so presentation is uniform — everything is equally visible, which adds noise in surfaces like Telegram where conversational flow matters more than process transparency. Introduce phase markers the agent emits to tag different kinds of work — at minimum research, execution, and response — propagated through the adapter as typed events. Channels consume the markers to style phases differently: collapse "boring work" sections, hide internal phases entirely, or suppress the final message when the whole turn was marked hidden (e.g., silent background-style activity). The canonical phase vocabulary and the rendering rules per channel (starting with Telegram) are part of the delta's scope. The marker emission mechanism should be evaluated during speccing — XML-style tags in the agent's text output (parsed by the adapter into typed events) are the preferred approach for simplicity, with agent tool calls as a heavier alternative if tags prove insufficient. The feature separates the agent's internal process from what the user sees and gives channels fine-grained control over message presentation without the agent having to format output differently per channel.

### DLT-151: Secure credential delivery to whitelisted commands
**Status**: ✗ Defined
**Depends on**: DLT-156
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: A mechanism where the agent can pipe passwords and credentials to specific pre-approved CLI commands without ever seeing the plaintext. The agent knows that a secret exists and which command needs it, but the actual value flows directly from a secrets store to the CLI's stdin or environment, never through the agent's context or output. This prevents credential leaks through conversation logs, tool outputs, or prompt injection. The whitelist is critical: only commands explicitly registered in configuration can receive secrets. The mechanism exposes a pluggable provider interface so different backends (1Password CLI SDK, Bitwarden CLI, local encrypted file) can be swapped via configuration. The delta delivers the injection mechanism, the whitelist configuration schema, the provider interface, and at least one concrete backend implementation. The encrypted token store provides the local backend that ships out of the box.

### DLT-153: Immediate steering mode for session tasks
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Medium
**Description**: Allow session tasks to fire immediately as steering messages into the active conversation, bypassing the idle wait. Currently, session tasks are gated by an idle check — they only fire when the user has been inactive for a configured period. This delta adds an optional immediate mode to session tasks that, when enabled, skips the idle check and injects the task's content as a steering message into the active conversation at the earliest opportunity. This enables reminder-like behavior where time-sensitive prompts reach the user without waiting for idle. The mode is configured per task definition and uses the existing steering message infrastructure.

### DLT-156: Encrypted token store for repo-committed secrets
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Tokens and other sensitive values currently sit as plaintext files under `.tachikoma/config/`, which means they are readable by the agent and end up in any transcript, context dump, or accidental commit. Introduce a secrets store where values live encrypted inside the repository and are accessed programmatically without ever exposing plaintext to the agent. Requirements: secrets live in the repo (encrypted, git-friendly), the store exposes a CLI suitable for piping values to command stdin or environment variables, the agent never sees plaintext at any layer (context, logs, tool output), and secret rotation is straightforward. Approaches to evaluate during speccing include age-encryption (simple, file-based, no daemon), SOPS (encrypted YAML/JSON with readable git diffs), and git-crypt (transparent per-pattern encryption). This delta delivers the store itself — encryption scheme, repo layout, CLI, and the access path used by downstream consumers such as the secure credential delivery mechanism (DLT-151).

### DLT-166: Detect stuck processes via output patterns
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: When running long-lived detached processes, users need early warning if a process becomes stuck (e.g., retry loops, repeated errors, or hung-state indicators) rather than discovering failure hours later. This delta adds a sentinel mechanism that watches process log files for configurable text patterns and dispatches a notification when a match is detected. Users define patterns per process or as global defaults via the agent tools interface. When a pattern fires, a notification is dispatched through the process monitoring system so the agent can investigate. The sentinel includes rate limiting to prevent notification floods from rapidly-repeating matches. This complements process exit detection by catching processes that are alive but not making progress.

### DLT-170: CLI for reading and writing configuration values
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Easy
**Description**: The agent currently reads and writes Tachikoma's TOML configuration file directly via Read/Write tools, which is error-prone — typos in nested keys, formatting drift, and accidental edits to comments are all easy to introduce. Add a CLI surface for inspecting and modifying configuration values that uses the typed settings model and the existing comment-preserving write-back infrastructure as its source of truth. The CLI exposes operations to read a value by dot-notation key (e.g. `agent.model`), update a value at a known key, and list values optionally scoped to a section. Keys are validated against the typed settings schema before reads or writes, and the underlying TOML file's comments and formatting are preserved on write. The CLI defaults to `~/.config/tachikoma/config.toml` but accepts a path override for use with non-default config locations. The exact shape (separate `tachikoma-config` binary versus a `tachikoma config` subcommand) should be evaluated during speccing, balancing entry point conventions against discoverability.

### DLT-171: Unified retention sweep with extension contributors
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Several transient tables grow without bound today: Telegram message↔session mappings accumulate one row per inbound and outbound message, closed conversation sessions are never deleted, recurring task instances pile up (only spent one-shot definitions are pruned), and exited detached-process records and soft-deleted workflow-state rows are never reclaimed. Meanwhile the retention that does exist is scattered across inconsistent triggers — one-shot task pruning on its own interval, stale-workflow cleanup at session close, transcript-file pruning in the memory maintenance schedule. Introduce a single retention sweep that runs on a fixed cadence plus a contributor extension point so each extension registers its own retention logic into the shared sweep instead of scheduling separate jobs; the host owns the tick and invokes every registered contributor with per-contributor error isolation. The existing scattered sweeps migrate onto this mechanism with no behavior change, and new contributors close the gaps: the tasks extension prunes both spent one-shot definitions and older terminal recurring instances, the workflows extension hard-deletes rows soft-deleted beyond a grace period, the core prunes old closed sessions together with their channel-message mappings, and the detached-processes extension reaps old exited records. Each contributor declares its own configurable age threshold. Two safety constraints are mandatory: any session or channel-message threshold must exceed the session resume window plus the bridging-context lookback so a still-resumable session is never deleted, and every deletion must be foreign-key-safe so a removed session takes its channel messages with it and no child rows are orphaned. The contributor-registration contract (where it attaches on the extension API, ordering relative to the tick, per-contributor error isolation), the per-contributor configuration surface and grace/threshold defaults, and whether the sweep runs as a host-owned cron or a registered scheduler job should be evaluated during speccing; the cross-extension ownership of the session→channel-message cascade (core deleting a Telegram-owned table versus Telegram contributing a cascade-aware contributor) is a key design question, and the retention-contributor pattern may warrant promotion to a design pattern if it proves reusable.

### DLT-172: Unified topic-oriented memory store
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 2 (High)
**Complexity**: Hard
**Description**: Replace the separate facts and preferences memory stores with a single topic-oriented store where each `<topic>.md` file holds everything known about a subject — objective reference facts and subjective preferences together — eliminating the error-prone classification boundary that fragmented that knowledge across two stores. Extraction folds both factual and preferential signals from a closed conversation into the relevant topic, creating or merging topic files rather than routing to a store, and each topic carries a single index entry. The static index injection and on-demand read pattern carry over, now scoped to topics. Episodic summaries and transcript archives are unaffected.

### DLT-173: Phased consolidation and pruning maintenance
**Status**: ✗ Defined
**Depends on**: DLT-172, DLT-123
**Priority**: 3 (Medium)
**Complexity**: Hard
**Description**: Restructure memory maintenance into explicit, phase-based background passes over the unified topic store and the learnings layer, replacing the current single daily/weekly per-store sweep with named phases that each have specific tasks. A consolidation pass runs in phases — orientation (read existing topics to establish baseline and avoid duplication), signal-gathering (scan recent sessions and episodic summaries for new material), and topic-merging (create or update topic files, normalizing relative temporal references like "yesterday" into absolute ISO dates). A separate pruning pass eliminates entries contradicted by newer information and collapses clusters of near-duplicate topics into a single clean replacement, preserving the oldest record's creation timestamp. Each phase commits its workspace edits. This expands the existing time-tiered episodic and topic maintenance into a coherent, layer-aware pipeline.

### DLT-174: Run a summarized subagent on demand via /boomerang
**Status**: ✗ Defined
**Depends on**: None
**Priority**: 3 (Medium)
**Complexity**: Medium
**Description**: Add a `/boomerang` command that lets the user manually spin up a subagent to handle a self-contained task, then folds a summary of the subagent's work back into the main conversation — the "boomerang" pattern (work goes out to an isolated agent; a condensed result returns). Functionally like the existing agent-triggered `delegate_to_agent`/subagent capability (a headless run with its own prompt and tool set), but invoked explicitly by the user as a command and with the subagent's transcript summarized rather than returned verbatim. Builds on the existing subagent runner (`skills/delegate.ts` / `SideRunner`) and the conversation-summarization primitives. Speccing will decide: how the task/agent is specified on the command (free-form task vs. a named skill-agent), how the summary is generated (follow-up summarization vs. pi's branch/compaction summary primitives) and injected back, and whether the run blocks or reports asynchronously. Complements DLT-094 (autonomous long-running agents) but is distinct: one-shot, manually triggered, and summarized.


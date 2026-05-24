# Core Architecture

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The core agent loop: receive a user message, pass it to the Claude agent via the SDK, and stream the response back as domain events. Channels (REPL, Telegram, etc.) call a single programmatic entry point and consume a uniform event stream, decoupled from SDK internals.

## User Stories

- As a developer, I want a programmatic entry point that accepts a message and streams back domain events so that I can build channels without knowing SDK details
- As a developer, I want conversation context preserved across messages so that follow-up messages are coherent
- As a user, I want my assistant to automatically inject relevant context before processing my message so that responses are informed without me repeating information

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Core agent loop: send a user message, receive a streamed response as domain events |
| R1 | Project structure: pyproject.toml with uv_build build system, `[project.scripts]` entry point, src package layout; subcommand-based CLI where bare `tachikoma` defaults to the `run` subcommand |
| R2 | Programmatic entry point (coordinator) that channels call to send messages and get streamed responses |
| R3 | Session lifecycle: create a per-message SDK client for each exchange, preserving conversation context across messages via resume-based session continuity. Optionally tracks sessions persistently via a session registry (see [sessions](sessions.md)) |
| R4 | Error handling: distinguish between transient failures that allow continued use and fatal failures that require stopping |
| R5 | Agent operates from workspace directory via SDK cwd option; optionally uses a custom CLI binary path (`cli_path`) |
| R6 | Post-processing pipeline: on session close, run registered processors in sequential phases to analyze the completed conversation and perform finalization tasks (see [pipeline spec](post-processing-pipeline.md)) |
| R7 | Agent has unrestricted tool access without user confirmation prompts |
| R8 | Tachikoma is the sole memory system — no competing memory mechanisms from the underlying SDK |
| R9 | Foundational context (personality, user knowledge, operational guidelines) loaded at startup as individual context file entries and persisted to the session database when the first message of a new session is processed; assembled into the system prompt from persisted entries on every SDK client creation |
| R10 | Conversation boundary detection: before processing a message, check whether it continues the current conversation or starts a new one; on topic shift, transition sessions before processing (see [boundary detection](boundary-detection.md)) |
| R11 | Per-message post-processing: after each agent response, trigger a per-message pipeline for ongoing conversation analysis (see [boundary detection](boundary-detection.md)) |
| R12 | Pre-processing pipeline: on new session, run registered context providers to enrich the first message before the agent processes it (see [pipeline spec](pre-processing-pipeline.md)) |
| R13 | Sub-agent delegation: coordinator receives detected agents from the pre-processing pipeline per-session and passes to SDK for delegation (see [skills](skills.md)) |
| R14 | Session resumption: on topic shift with a matching recent session, reopen the matched session instead of creating a fresh one; inject bridging context from intermediate sessions; skip pre-processing (resumed SDK session has full prior context) |
| R15 | MCP server registration: coordinator accepts an optional mapping of named MCP server configurations at construction and passes them to `ClaudeAgentOptions` |
| R16 | Last message time tracking: coordinator tracks the timestamp of the last message exchange for idle gating by external subsystems (consumed by the priority buffer — see [delivery/priority-buffer](../delivery/priority-buffer.md)) |
| R19 | Coordinator idle signaling: on every busy→idle transition, the coordinator dispatches a `CoordinatorIdle(timestamp)` event so the priority buffer can re-evaluate pending deliveries without polling |
| R17 | Configurable tool blocking: specific tools can be unconditionally blocked via `disallowed_tools` config, defaulting to `["AskUserQuestion", "CronCreate", "CronDelete", "CronList"]` |
| R18 | Text sanitization: the adapter strips invalid UTF-8 characters (surrogate code points, overlong encodings) from all SDK text before passing to domain types, preventing encoding failures in downstream consumers |
| R20 | Deferred message queue: coordinator provides a deferred message queue (`enqueue_deferred`, `has_deferred`, `promote_next_deferred`) for messages that should wait until the current turn completes; message envelopes expose a `force_new` hook that, when True, causes the coordinator to skip boundary detection and force a fresh session transition |
| R21 | Typed message envelope: the coordinator's message-buffer / deferred-queue / per-turn inbox carry a `MessageEnvelope` abstract base with property hooks for the per-kind behavior consumers depend on — `sdk_input` (the string yielded to the SDK), `pinned_skills`, `force_new`, and `runs_pre_processing`. Concrete subtypes include `TextMessage` (text + pinned_skills + force_new) and `ButtonTapMessage` (value, with `runs_pre_processing=False` so the per-message pre-processing pipeline is skipped for taps). SDK-input shaping happens at a single rendering site reading `envelope.sdk_input`. Producers (channels, task executor, priority-buffer consumers) construct envelopes at the coordinator boundary. See [DES-013](../../design/DES-013-typed-envelope-with-property-hooks.md) |

## Behaviors

### Message Processing (R0)

The coordinator receives a text message, forwards it to the SDK client, and yields domain events as the agent responds.

**Acceptance Criteria**:
- Given a user message, when passed to the coordinator, then the agent responds via the Claude model and the response streams as domain events
- Given a conversation in progress, when the user sends a follow-up message, then the agent has context from prior messages in the same session (R3)
- Given a user message, when boundary detection or a pre-processing pipeline runs, then each component reports what it is doing via a status callback and the coordinator forwards those messages as granular `Status` AgentEvents on the stream before the SDK response begins (e.g. `"Analyzing message..."`, `"Searching memories..."`, `"Detecting relevant skills..."`); completion messages (e.g. `"Found 3 relevant memories"`, `"No relevant skills detected"`) are also emitted after each provider finishes
- Given a user message, when boundary detection is active and a topic shift is detected, then a session transition occurs before the message is processed in a fresh context (R10)
- Given a conversation, when the user asks about files in the working directory, then the agent can explore and report on them

### Programmatic Entry Point (R2)

Channels interact with the agent through a single coordinator interface that returns an async event stream.

**Acceptance Criteria**:
- Given a channel implementation, when it calls the coordinator with a user message, then it receives an async iterator that yields domain events as the response streams
- Given the coordinator produces events, when a channel consumes them, then only meaningful domain events are surfaced (internal SDK messages are filtered)

### Session Lifecycle (R3)

The coordinator manages per-message SDK client creation and maintains conversation context via resume-based session continuity.

**Acceptance Criteria**:
- Given a user message, when the coordinator processes it, then a fresh SDK client is created for that exchange and disposed after the response completes
- Given a conversation in progress, when the user sends a follow-up message, then the coordinator resumes the existing SDK session via `resume=sdk_session_id`
- Given a new conversation starts, then a new session is created
- Given an active session, when subsequent messages arrive, then they use the same session
- Given a session registry is available, when the first message in a new conversation arrives, then a persistent session is created before the message is processed
- Given an active persistent session, when the agent produces a Result event, then the session's SDK metadata (session ID and transcript path) is populated
- Given an active persistent session, when the coordinator exits its async context, then the persistent session is closed
- Given a session registry operation fails, then the error is logged and the conversation continues uninterrupted
- Given a topic shift is detected mid-conversation, when the transition completes, then the current session is closed, the SDK session ID is cleared, and a new session is created — the next message starts a fresh SDK session with the previous conversation summary in the system prompt
- Given a session transition where any step fails, when the transition completes, then a new session is still created in the registry

### Working Directory (R5)

The coordinator operates from the workspace directory, ensuring the agent's file operations are relative to the configured workspace path.

**Acceptance Criteria**:
- Given the agent starts, when the coordinator is created, then its working directory is set to the workspace path from configuration
- Given a custom workspace path is configured, when the agent starts, then the coordinator's working directory matches that custom path

### Startup Validation (R4)

Fatal errors at startup are caught before the main loop begins.

**Acceptance Criteria**:
- Given missing or invalid authentication, when the coordinator attempts to connect, then the process exits with a clear error message

### Post-Processing Pipeline Trigger (R6)

After a session closes, the coordinator triggers a registered post-processing pipeline that runs processors in phases to analyze the completed conversation and perform finalization tasks.

**Acceptance Criteria**:
- Given a session closes with a valid SDK session ID and a pipeline is registered, when the coordinator exits its async context, then the pipeline runs registered processors in phases, with processors within each phase running in parallel
- Given a session closes without an SDK session ID, when the coordinator would trigger post-processing, then the pipeline is skipped and a warning is logged
- Given no active session exists, when the coordinator exits its async context, then the pipeline is not triggered
- Given a pipeline processor fails, when other processors are running, then the failing processor's error is logged and the others complete normally
- Given the pipeline itself fails, when the coordinator is shutting down, then the error is logged and shutdown continues
- Given no pipeline is registered, when a session closes, then shutdown proceeds directly
- Given a pipeline is already running from a previous session close, when another session close triggers the pipeline, then the new run is serialized (awaits the previous one before starting)
- Given a session closes mid-conversation due to a topic shift, when the session has a valid SDK session ID and a pipeline is registered, then the pipeline runs asynchronously as a background task (not blocking the new session)
- Given background post-processing tasks are running from previous topic shifts, when the coordinator shuts down, then it awaits all background tasks before exiting
- Given a background post-processing task fails, when the error occurs, then it is logged without affecting the active conversation or other background tasks
- Given a main-phase processor fails during pipeline execution, when the finalize phase begins, then the finalize-phase processors still run (error isolation applies across phases)
- Given a post-processing processor forks the conversation, when the forked session is created, then it has `permission_mode="bypassPermissions"` to operate without user confirmation

### Unrestricted Tool Access (R7)

The agent operates with full tool access, bypassing Claude Code's default permission prompts.

**Acceptance Criteria**:
- Given the coordinator is created, then `ClaudeAgentOptions.permission_mode` is set to `"bypassPermissions"`

### Configurable Tool Blocking (R17)

Specific tools can be unconditionally blocked via a configurable list. This prevents the agent from using tools that conflict with Tachikoma's autonomous operation model (e.g., `AskUserQuestion` triggers interactive user prompts).

**Acceptance Criteria**:
- Given default config, when the coordinator builds options, then `ClaudeAgentOptions.disallowed_tools` contains `["AskUserQuestion", "CronCreate", "CronDelete", "CronList"]`
- Given a custom `disallowed_tools` list in config, when the coordinator builds options, then `ClaudeAgentOptions.disallowed_tools` matches the configured list

### Auto-Memory Disabled and Configurable Env (R8)

Claude Code's built-in auto-memory feature is disabled so that Tachikoma's own memory system (context files + post-processing extraction) is the sole memory mechanism. Users can pass custom environment variables to all SDK sessions via the `[agent.env]` config section. The environment has two categories of automatic defaults: **hardcoded defaults** (like `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`) which cannot be overridden — collisions raise a startup error; and **auto-injected defaults** (like `TZ` from the configured timezone) which are silently overridable by user env. The system prompt date command does not include a `TZ=` prefix since the environment handles timezone.

**Acceptance Criteria**:
- Given the coordinator is created, then `ClaudeAgentOptions.env` includes `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
- Given `[agent.env]` contains custom values, when any SDK session is created (coordinator, sub-agents, post-processors), then `ClaudeAgentOptions.env` includes auto-injected defaults, hardcoded defaults, and the custom values
- Given `[agent.env]` contains a key that collides with a hardcoded default, when the application starts, then it exits with a clear error
- Given a configured timezone, when the agent environment is constructed, then `TZ` is set to the configured IANA timezone string
- Given a user sets `TZ` in `[agent.env]`, when the agent environment is constructed, then the user's value wins and no error is raised (auto-injected defaults are silently overridable)
- Given the system prompt is rendered, when the date command is displayed, then it does not include a `TZ=` prefix (timezone is in the subprocess environment)

### Foundational Context (R9)

Personality, user knowledge, and operational guidelines are loaded at startup as individual context entries (one per file: soul, user, agents) and persisted to the database when the first message of a new session is processed. The system prompt is assembled from persisted entries before every SDK client creation.

**Acceptance Criteria**:
- Given the coordinator is created with foundational context entries (one per context file), when the first message of a new session is processed, then each entry is saved to the database as a context entry (best-effort — failures are logged, not propagated)
- Given foundational context files exist, when the bootstrap hook loads context, then each file is identified by its owner tag (soul, user, agents) and its content, ready for persistence
- Given foundational context entries are persisted, when the system prompt is assembled, then each entry appears wrapped in XML tags by owner
- Given all foundational context files are missing or empty, when a session starts, then no foundational entries are created
- Given context files are updated by post-processing after a session close, when the next session starts, then the coordinator loads the updated files — context changes take effect on the next session (see [core-context-updates](core-context-updates.md))

### Pre-Processing Pipeline Trigger (R12)

On the first message of a new session, the coordinator triggers a registered pre-processing pipeline that runs context providers in parallel to enrich the message before the agent sees it.

**Acceptance Criteria**:
- Given one or more context providers are registered and a new session starts, when the first message arrives, then the pipeline runs all providers in parallel before the coordinator passes the message to the agent
- Given the pipeline completes with results, when the coordinator processes the message, then each successful result is persisted as a context entry (owner=result.tag, content=result.content) and appears in the assembled system prompt — not prepended to the message text
- Given a subsequent message arrives in the same session, when the coordinator processes it, then pre-processing is skipped — the agent already has context from the first enriched message in its conversation history
- Given no pre-processing pipeline is registered, when a new session starts, then the message is sent to the agent unmodified
- Given the pre-processing pipeline fails, when the coordinator handles the error, then the failure is logged and the original unmodified message is sent to the agent
- Given session creation fails, when the coordinator would run pre-processing, then pre-processing is skipped
- Given all providers fail or return no results, when the coordinator processes the message, then the original message is sent to the agent unmodified
- Given context providers return `mcp_servers` in their results, when the coordinator processes pipeline results, then it extracts and merges all `mcp_servers` and stores them per-session for inclusion in `ClaudeAgentOptions`

### Context Assembly (R9, R12)

The system prompt is assembled from persisted database entries on every SDK client creation, making the database the canonical source of context.

**Acceptance Criteria**:
- Given a session with persisted entries, when an SDK client is created, then the system prompt append is assembled from: the base system preamble (rendered with the configured timezone; includes identity, role, and memory guidance) + persisted entries (each wrapped in XML tags by owner, in the order they were persisted)
- Given the coordinator creates a client for subsequent messages in a session, then context is loaded from the database on each client creation — not from in-memory state
- Given context loading fails during prompt assembly, then the error is logged and the system prompt falls back to the base system preamble only (no dynamic context entries)
- Given a fork helper is called with a system prompt append parameter, then the assembly function builds the system prompt append from the parent session's entries, set on the fork's options
- Given a fork helper is called without a system prompt append parameter (default), then behavior is unchanged — no system prompt context injected

### Sub-Agent Delegation (R13)

The coordinator receives detected agents from the pre-processing pipeline per-session and passes them to the SDK for delegation.

**Acceptance Criteria**:
- Given a new session starts and the pre-processing pipeline returns results containing agent definitions, when the coordinator processes the first message, then it extracts agent definitions from the results and stores them for the session
- Given agents are stored for a session, when subsequent messages arrive, then the coordinator passes the same agents to `ClaudeAgentOptions.agents` (SDK handles delegation logic)
- Given no agents are detected (no skills relevant or no skills exist), when the coordinator creates ClaudeAgentOptions, then no sub-agents are available for delegation
- Given a topic shift causes a new session, when the session transition completes, then agents are cleared and re-detected from the pre-processing pipeline on the next message

### Error Recovery (R4)

Transient errors keep the conversation usable. Fatal errors signal channels to exit. When SDK subprocess errors occur, stderr output from the subprocess is captured and included in error logs for operator diagnosis.

**Acceptance Criteria**:
- Given a transient connection error mid-stream, then an error event with `recoverable=True` is produced and the conversation remains usable; partial output remains visible
- Given an in-stream rate limit or server error, then an error event with `recoverable=True` is produced
- Given an in-stream authentication or billing error, then an error event with `recoverable=False` is produced
- Given an SDK subprocess error, when stderr output was produced by the subprocess, then the error log entry includes the captured stderr as a structured field

### Boundary Detection Gating (R10)

Before processing a message, the coordinator checks whether it continues the current conversation or starts a new topic. On topic shift, it orchestrates a session transition before the message reaches the SDK.

**Acceptance Criteria**:
- Given an active session with a conversation summary, when a new message arrives, then the coordinator fetches recent closed session candidates and runs boundary detection (with candidates) before processing the message
- Given a topic shift is detected with no matching session, when the transition completes, then the current session is closed, MCP servers are cleared, a new session is created with the SDK context reset, and the previous summary is persisted as a context entry for the new session. The message is processed in the fresh session (MCP servers are re-extracted from pre-processing results)
- Given a topic shift is detected with a matching session, when the transition completes, then the current session is closed, the matched session is reopened, bridging context from intermediate sessions is persisted as a context entry for the resumed session, and the message is processed in the resumed session context
- Given a session is resumed, when the coordinator processes the next message, then pre-processing is skipped (the resumed SDK session has full prior context) and the bridging context appears in the system prompt (persisted for the session's lifetime, not just the first message)
- Given intermediate sessions exist between the matched session's last close and now, when bridging context is assembled, then their summaries are concatenated chronologically and persisted as a context entry
- Given no intermediate sessions exist, when bridging context is assembled, then no bridging context entry is created
- Given the resume path fails (session not found, already open, reopen error), when the coordinator handles the failure, then it falls back to the fresh-session path with a warning log
- Given candidate fetching fails, when the error is caught, then boundary detection proceeds without candidates (fail-open)
- Given no active session, no summary, or no workspace directory exists, when a message arrives, then boundary detection is skipped
- Given boundary detection fails, when the error is caught, then the message proceeds as a continuation (fail-open)

### Per-Message Post-Processing Trigger (R11)

After each agent response completes, the coordinator triggers a per-message pipeline as a background task to update conversation state (rolling summary).

**Acceptance Criteria**:
- Given the agent completes a response, when the response stream ends, then the per-message pipeline runs asynchronously with the current session (with SDK metadata populated) and the accumulated response text
- Given no per-message pipeline is registered, when the response completes, then no per-message processing runs

### MCP Server Registration (R15)

The coordinator accepts an optional mapping of named MCP server configurations at construction and passes them to `ClaudeAgentOptions` for every message exchange.

**Acceptance Criteria**:
- Given the coordinator is created with `mcp_servers`, when `_build_options()` constructs per-message options, then the MCP servers are included in `ClaudeAgentOptions.mcp_servers`
- Given the coordinator is created without `mcp_servers` (None), when `_build_options()` runs, then no MCP servers are passed to the SDK
- Given the coordinator is created with both task-tools and workflow-tools MCP servers, when `_build_options()` runs, then both servers are available to the agent (see [task management](../../tasks/task-management.md) and [workflows](../../workflows/workflow-state-machine.md))

### Last Message Time Tracking (R16)

The coordinator tracks the timestamp of the last message exchange, updated on both send and response completion. External subsystems read this property for idle gating — in particular, the priority buffer uses it to gate new-turn deliveries (see [delivery/priority-buffer](../delivery/priority-buffer.md)).

**Acceptance Criteria**:
- Given a user message is sent via `send_message()`, when the call begins, then `last_message_time` is updated to the current time
- Given the agent completes a response, when the Result event is produced, then `last_message_time` is updated to the current time
- Given an external subsystem reads `last_message_time`, then it receives the timestamp of the most recent message exchange

### Coordinator Idle Signaling (R19)

Whenever the coordinator transitions from busy to idle (an exchange completes or the queue drains), it dispatches a `CoordinatorIdle(timestamp)` event on the bus. The priority buffer subscribes to this event to wake from its wait and re-evaluate delivery conditions without needing to poll the coordinator.

**Acceptance Criteria**:
- Given the coordinator finishes processing an exchange and `is_busy` becomes False, when the busy→idle transition is observed, then a `CoordinatorIdle` event is dispatched on the bus with the transition timestamp
- Given the coordinator is already idle when an operation completes (no state change), then no `CoordinatorIdle` event is dispatched
- Given the event bus is unavailable at the coordinator, when the transition occurs, then the transition proceeds without error (idle signaling is best-effort)

### Deferred Message Queue (R20)

The coordinator provides a deferred message queue for messages that should wait until the current turn completes. Channels use this to hold command messages (`/new`, `/queue`) that should not be steered into the active session. The queue is ephemeral and in-memory (lost on process crash, drained on graceful shutdown).

Message envelopes expose a `force_new` hook on the base. When True, the coordinator skips boundary detection and unconditionally triggers a fresh session transition — closing the current session (with async post-processing) and creating a new one before processing the message.

**Acceptance Criteria**:
- Given a message with `force_new=True` and an active session exists, when the coordinator processes it, then boundary detection is skipped, the current session is closed (with async post-processing), and a new session is created
- Given a message with `force_new=True` and no active session, when the coordinator processes it, then a new session is created normally (no transition needed)
- Given a message is enqueued via `enqueue_deferred()`, when the channel calls `promote_next_deferred()`, then the message moves from the deferred queue to the message buffer
- Given the deferred queue has items, when `has_deferred` is checked, then it returns True
- Given the coordinator has deferred messages, when `is_busy` is checked, then it returns True (deferred items count as busy)

### Typed Message Envelope (R21)

The coordinator's message-buffer, deferred queue, and per-turn SDK inbox all carry the abstract `MessageEnvelope` base. Concrete subtypes own their per-kind behavior via property hooks (`sdk_input`, `pinned_skills`, `force_new`, `runs_pre_processing`) — consumers (pipelines, boundary detector, SDK rendering site) read the hooks without `isinstance`-checking the subtype. SDK-input shaping happens at a single site reading `envelope.sdk_input`, so structure survives all the way to the SDK boundary and every message path (initial, steered, re-queued) renders identically.

**Acceptance Criteria**:
- Given a `TextMessage` envelope with text and optional `pinned_skills` / `force_new`, when it flows through the coordinator, then `sdk_input` returns the text as-is, boundary detection consumes the text, the skill classifier honors `pinned_skills`, and `force_new=True` skips boundary detection
- Given a `ButtonTapMessage` envelope carrying a value, when it flows through the coordinator, then `sdk_input` renders an explicit prose framing the agent recognizes as a tap, `runs_pre_processing` returns False so both pre-processing pipelines are skipped, and boundary detection still runs on `sdk_input`
- Given a producer site (REPL, Telegram text/media/commands, priority-buffer delivery, task executor), when it enqueues a message at the coordinator boundary, then it constructs a `TextMessage` envelope carrying the same fields it previously passed (`text`, `pinned_skills`, `force_new`)
- Given an envelope reaches the coordinator's `_message_source`, when it is shaped into SDK input, then it passes through one rendering site — initial, steered, and re-queued envelopes all use the same path

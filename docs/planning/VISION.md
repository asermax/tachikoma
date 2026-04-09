# Project Vision: Tachikoma

**A proactive personal assistant that remembers, learns, and takes initiative.**

*Not just responding to commands — anticipating needs, managing context, and evolving through use.*

## Problem

Current AI assistants are stateless and reactive. Every conversation starts from zero, the user must provide all context, and the assistant never acts unless asked. This creates a gap between what AI can do and what a truly helpful personal assistant should be.

**Who experiences this:**
- Users who interact with AI daily for personal productivity (notes, tasks, emails, planning)
- Anyone who wants an assistant that knows them — their preferences, ongoing projects, communication style
- People who need proactive help surfacing information and managing tasks without constant manual prompting

**Current situation:**
- **ChatGPT/Claude**: Powerful but stateless per-session; memory features are shallow (key-value facts, not real understanding)
- **OpenClaw**: Tested — good infrastructure (Telegram, cron jobs) but the agent itself is too basic; waits for instructions, doesn't take initiative
- **Custom agent frameworks (LangChain, CrewAI)**: Provide primitives but don't encode personal assistant patterns; still reactive by default
- **Obsidian + Claude Code (prior workflow)**: Worked well for vault management but lacked persistent memory, proactive behavior, and always-on availability

**What's needed:**
An opinionated personal assistant built on Claude Agent SDK that maintains conversation continuity across messages, enriches every interaction with accumulated context (memories, skills, project state), and autonomously processes tasks on schedules — all accessible through a simple chat interface.

## Core Workflows

### 1. Contextual Conversation

**Trigger**: User sends a message via Telegram or the terminal REPL
**Steps**:
1. On a new conversation, the pre-processing pipeline enriches the session with context gathered in parallel: memory provider retrieves relevant past interactions, skills provider detects applicable specialized agents, and projects provider injects current project state and management tools
2. The coordinator passes the enriched message to the Claude Agent SDK, which maintains conversation continuity across messages within the session
3. When skills are detected, their specialized agents are available for delegation — the assistant can hand off focused work to domain-specific agents during the conversation
4. Response is streamed back to the user through the active channel

**Result**: Every conversation benefits from the assistant's accumulated knowledge about the user, relevant skills, and project context

### 2. Memory Extraction

**Trigger**: Conversation boundary detected (topic shift on a new message, or idle timeout)
**Steps**:
1. On each incoming message, boundary detection analyzes whether the user is continuing the current topic or shifting to a new one; if a shift is detected, it also checks whether the new topic matches a recent past conversation that can be resumed
2. Session closure triggers the post-processing pipeline with the completed conversation
3. Separate processors run in parallel, each extracting a different type of learning: episodic summaries (date-stamped conversation overviews), facts (knowledge about the user and the world), preferences (user likes, dislikes, and working style), and core context updates (refinements to the assistant's personality and understanding)
4. Extracted memories are stored as written documents organized by type — not embeddings or key-value pairs — preserving nuance and context
5. The workspace is version-tracked after each session, creating a history of all changes for rollback and auditing
6. Memories are available for retrieval in future conversations via the pre-processing pipeline

**Result**: The assistant learns from every interaction without explicit user action

### 3. Project Management

**Trigger**: User asks to work with external codebases, or the agent needs project context
**Steps**:
1. Projects are registered as tracked repositories in the workspace, each with its name, path, and current branch
2. On startup, all registered projects are synchronized to their latest state automatically
3. During conversations, the projects context provider surfaces current project state and exposes tools for registering and deregistering projects
4. The agent can work across multiple codebases during a conversation, with each project's files directly accessible
5. On session close, changes in each project are automatically committed with descriptive messages and pushed to their remotes

**Result**: External codebases are managed, tracked, and version-controlled without manual git operations

### 4. Proactive Task Processing

**Trigger**: Scheduled time arrives, or the user creates a task during conversation
**Steps**:
1. Tasks are defined with cron schedules (recurring) or one-shot datetime targets, and the agent can create and manage them conversationally via tools
2. **Session tasks** execute during the user's active session when idle — gated by a configurable idle period so they never interrupt active conversation. Results are delivered as proactive messages through the active channel
3. **Background tasks** execute autonomously in their own sessions, independent of whether the user is chatting. They run iteratively until the work is assessed as complete, and produce notification messages summarizing what was done
4. Task schedules support timezone-aware cron expressions and survive restarts with catch-up for missed runs

**Result**: The assistant works autonomously on scheduled and ad-hoc tasks, delivering results as notifications or proactive messages at appropriate times

### 5. Skill-based Specialization

**Trigger**: User's message matches a domain where specialized knowledge or agents are available
**Steps**:
1. Skills are packages that bundle domain expertise — each contains a description (used for detection), instructions (injected into context), and optionally specialized agent definitions
2. During pre-processing, an LLM classifies which skills are relevant to the current message based on their descriptions
3. Matched skills' instructions and agents are loaded into the session — the assistant gains domain-specific knowledge and can delegate focused work to skill agents
4. Skill detection is per-session: once a skill is activated, it remains available for the entire conversation

**Result**: The assistant adapts its capabilities to the topic at hand, drawing on packaged expertise and specialized agents as needed

## Scope

### v1 — Built

**Agent Core:**
- Coordinator built on Claude Agent SDK with per-message processing and conversation continuity via session resumption
- Telegram bot and terminal REPL as communication channels
- Conversation boundary detection via LLM-based topic shift analysis with session resumption matching
- Session tracking with lifecycle management, rolling summaries, and resumption history
- Core context files: SOUL.md (personality/tone), USER.md (user information), AGENTS.md (agent instructions)
- First-run workspace initialization with default directory structure and context files
- Git-managed workspace with automatic commits after each session for version history and rollback
- TOML-based configuration with validation and auto-generated defaults on first run
- Bootstrap system with ordered, idempotent initialization hooks per subsystem
- Structured logging throughout all subsystems

**Pre/Post Processing Pipelines:**
- Pre-processing: parallel context providers (memory, projects, skills) with error isolation — individual failures don't block the conversation
- Session post-processing: phased parallel processors (memory extraction, core context updates, project commits, workspace versioning) triggered on session close
- Per-message post-processing: asynchronous rolling summary generation after each agent response, used for boundary detection

**Skills System:**
- Directory-based skill packages with metadata, instructions, and optional agent definitions
- LLM-based skill detection during pre-processing
- Skill content and specialized agents loaded into session context when matched

**Memory System:**
- Memories stored as markdown files organized by type: episodic, facts, preferences
- Parallel extraction processors via session forking after conversation close
- Agent-driven memory search during pre-processing for context retrieval

**Projects:**
- Multi-repository management with automatic synchronization on startup
- Context injection with project state and tools for registration and deregistration
- Automatic commit with descriptive messages and push to remotes on session close

**Task System:**
- Cron-based and one-shot scheduling with timezone support
- Session tasks: idle-gated, delivered through the active channel
- Background tasks: autonomous sessions with iterative completion evaluation
- Tools for the agent to create, update, and manage task definitions conversationally
- Notification generation from completed background tasks

**Workflows:**
- Directory-based workflow definitions with ordered steps, instructions, references, and scripts
- Workflow state machine with database-persisted step states (pending, started, completed, skipped)
- MCP tools for full lifecycle management: start, advance, query, end, and list workflows
- Stale workflow cleanup processor integrated into session post-processing
- Built-in workflow authoring guide skill

**Media:**
- Full Telegram media support: photos, audio, voice messages, documents, stickers, video, video notes, and animations
- Media descriptor table with type-specific metadata builders and extension resolvers
- Download with file size validation, unique filename generation, and temp directory lifecycle management

**Notifications:**
- Event-bus-driven notification system with severity levels (info, error)
- Dispatch helper and structured prompt builder for notification delivery
- MCP tool server factory for agent-driven notifications during background task execution

**Event Bus:**
- bubus-based event bus for cross-subsystem communication (skill changes, session tasks, notifications)
- Decouples producers from consumers across the coordinator, task system, skills watcher, and channels

**Channel Protocol:**
- Generic Channel protocol with capability discovery (MCP servers, skill sources) and run loop
- Enables pluggable channels beyond the built-in REPL and Telegram implementations

**Context Persistence:**
- Session context entries persisted to database with structured metadata
- Context reconstruction across per-message SDK client recreations
- Assembly from persisted entries with owner-tagged XML sections

**Cold-Start Session Resumption:**
- Resume previous conversations on fresh startup by matching incoming messages against recent closed sessions
- Preserves conversational continuity across process restarts

**SDK Transport:**
- Custom transport that passes system prompts via tempfile to work around OS ARG_MAX limits
- Transparent to all call sites via the shared agent defaults layer

**Release Pipeline:**
- Semantic versioning with conventional commits (python-semantic-release)
- Automated changelog generation and PyPI-compatible builds via uv

**Built-in Skills:**
- Skill authoring guide: teaches the agent how to scaffold new skills with proper structure and metadata
- Workflow authoring guide: teaches the agent how to create workflow definitions with steps and references

**Session Idle Timeout:**
- Configurable auto-close of idle sessions (default 900 seconds) so post-processing triggers without requiring a topic shift

**Hot-Reload Skills:**
- Filesystem watcher (watchfiles) monitors the skills directory for changes
- Marks the registry dirty and dispatches SkillsChanged event for immediate availability

**Telegram Push Notifications:**
- Configurable push notification support via copy+delete mechanism for post-response alerts
- Users are notified when the agent has responded or a background task completed

**Per-Message Pre/Post Processing:**
- Per-message pre-processing pipeline for context evaluation on every message (skills, memory), distinct from the session-gated pipeline (projects)
- Per-message post-processing for rolling summary generation after each agent response

### v1 — Pending

**Critical:**
- Capture SDK stderr on error for debugging — attach stderr output from CLI subprocess to error logs when SDK calls fail (DLT-098)

**High:**
- Granular processing status messages — replace "Thinking..." with component-driven status updates during pipeline processing (DLT-031)
- Collapse intensive work sections in Telegram — detect rapid tool-call sequences and wrap intermediate content in collapsible sections (DLT-064)
- Recover interrupted post-processing on restart — checkpoint tracking so incomplete post-processing resumes from where it left off (DLT-066)
- Abort tool execution on stop steering message — immediate interrupt on stop intent instead of waiting for the tool chain to complete (DLT-089)
- Delegate work to autonomous long-running agents — persistent communicative agents that execute extended work with progress reporting and user interaction (DLT-094)
- Keep local repositories in sync with remotes — fetch-rebase-push sequence replacing bare push, with conflict recovery (DLT-097)
- Archive conversation transcripts to workspace — copy transcripts from SDK storage to workspace for durability and accessibility (DLT-099)
- Agent-driven episodic memory search — MCP tools for on-demand memory search by keyword, date range, or relevance during conversations (DLT-105)
- Prevent message loss during response finalization — guarantee every consumed message is processed even during response transitions (DLT-111)
- Buffer background task notifications until idle — hold notifications for natural conversation pauses instead of injecting into active exchanges (DLT-112)
- Fix double 'and' in truncated tool activity summary — grammar fix for truncated summaries (DLT-113)
- Run and monitor detached shell commands — MCP tools for dispatching, monitoring, and controlling OS-level commands that outlive the session (DLT-115)
- Git-friendly database storage — replace opaque binary SQLite diffs with diffable representations in git history (DLT-121)

**Medium:**
- Run as a persistent background service — systemd-managed process with auto-start and crash recovery (DLT-011)
- Summarize agent actions in Telegram responses instead of generic tool markers

**Low / Backlog:**
- LLM observability for tracking token usage, latency, and costs across all agent interactions (DLT-014)
- Base evaluation framework for testing agent processing pipelines (DLT-015)
- Semantic similarity search for memory retrieval (embedding-based, replacing keyword search) (DLT-009)

## Technical Context

**Platform:**
- Linux (primary development target)
- Runs as a persistent background service
- Single-user, self-hosted

**Language/Runtime:**
- Python (Claude Agent SDK has a Python SDK)
- Installable as a CLI tool via uv

**User Interaction:**
- Telegram Bot API as primary interface
- Terminal REPL for local development and direct interaction
- Text, images, audio, voice, documents, stickers, video, and animations via Telegram
- Text-based conversation via terminal REPL

**Agent Framework:**
- Claude Agent SDK for agent orchestration
- Per-message SDK client with conversation continuity via session resumption
- Session forking for post-processing and background task execution
- Skills system for specialized agent delegation
- Event bus (bubus) for decoupled cross-subsystem communication
- Workflow state machines with database persistence and MCP tool lifecycle management
- Custom SDK transport for system prompt delivery via tempfile (ARG_MAX workaround)

**Workspace:**
- A git-managed directory containing all persistent data (memories, core context files, skill definitions, configuration)
- Changes committed automatically for version history and rollback
- Markdown files for memories, with agent-driven search for retrieval
- SQLAlchemy with aiosqlite for session, task, workflow, and context tracking; Alembic for migrations
- Context persistence database for session context entries with structured metadata
- Event-bus-driven notification delivery with severity-based routing

**External Systems:**
- Telegram Bot API (communication)
- Anthropic API via Claude Agent SDK (agent execution)
- Local filesystem (git-managed workspace)

**Configuration:**
- Environment variables for API keys (Telegram, Anthropic)
- TOML config file with Pydantic validation and auto-generated defaults for tunable parameters
- Semantic versioning with conventional commits and automated changelog generation

## Success Criteria

### Achieved

1. Contextual conversations via Telegram and REPL that reference past interactions through automatic memory retrieval
2. Automatic memory extraction (episodic, facts, preferences) after conversations close, surfaced in future sessions
3. Skill-based specialization with LLM detection and specialized agent delegation
4. Proactive task scheduling with cron-based and one-shot tasks, session-aware and background execution modes
5. Multi-project management with automatic synchronization, context injection, and commit/push on session close
6. Git-versioned workspace with automatic commits after every session
7. Conversation boundary detection with topic shift analysis and session resumption
8. Workflow engine with directory-based definitions, step state tracking, and agent-driven lifecycle management via MCP tools
9. Full Telegram media support (photos, audio, voice, documents, stickers, video, animations)
10. Session idle timeout with automatic post-processing trigger — no topic shift required
11. Hot-reload skills with filesystem watching for immediate availability of new or modified skills
12. Telegram push notifications for post-response alerts and task completion
13. Event-bus-driven notification system for background task results and cross-subsystem communication
14. Cold-start session resumption preserving conversational continuity across process restarts
15. Installable CLI tool via uv with semantic versioning and automated releases

### Still Needed for v1

1. Capture SDK stderr on error for operator debugging (DLT-098)
2. Readable action summaries in Telegram responses instead of generic tool markers
3. Granular processing status messages replacing the generic "Thinking..." indicator (DLT-031)
4. Persistent background service with auto-start and crash recovery (DLT-011)
5. Prevent message loss during response finalization (DLT-111)

## Future Considerations

Ideas for v2 and beyond (not committing to these):

**Advanced Memory:**
- ACE Framework cycle: async session evaluation, pattern consolidation, memory curation
- Embedding-based semantic similarity search for retrieval (vs. keyword/agent-driven search)
- Contradiction detection and resolution
- Memory decay and archival

**Context Providers (beyond memory):**
- Calendar provider (Google Calendar)
- Tasks provider (Google Tasks)
- Email provider (Gmail)
- Notes provider (Obsidian vault)
- Dynamic/user-created providers via plugin system

**Plugin System:**
- Directory-based plugins contributing context providers, post-processors, skills, channels, and tools
- Plugin install, update, and removal lifecycle
- Skill-provided tools via MCP servers

**Context Lifecycle:**
- Persisted context entries with invalidation and refresh when underlying data changes
- Foundational context as a pre-processing provider with file-change invalidation
- Proactive session handoff before context compaction to preserve critical context

**Channels and Interfaces:**
- Concurrent secondary channels alongside the primary (e.g., Telegram notifications while using REPL)
- Web interface with chat and dashboard
- Hardware presence (speaker with simple display)

**Autonomous Agents:**
- Delegate work to persistent, communicative long-running agents that maintain ongoing sessions
- Agents report progress, ask clarifying questions, and collaborate with the user over time
- User can control lifecycle (pause, resume, terminate) without blocking main conversation
- Pause background tasks to request user input mid-execution

**Transcript Archiving and Search:**
- Archive conversation transcripts to workspace for durability independent of SDK storage
- Search and analysis tools for past conversations (full-text search, date filtering, excerpt retrieval)

**Operational Tooling:**
- CLI for querying internal state (tasks, sessions, context entries, skill status) without starting a full agent conversation
- External command processor for remote management of server deployments without SSH
- Run and monitor detached shell commands as a lightweight process supervisor
- Check for agent updates and notify user with confirm/defer/skip choices

**Advanced Proactivity:**
- Event-driven triggers from external sources (new emails, calendar events)
- Pattern detection and insight surfacing
- Dynamic profile building
- Sensor framework for proactive nudge signals with pluggable sensors and user-facing configuration
- Memory sensor for detecting follow-up-worthy topics from past conversations

**Other:**
- Feature toggles for disabling optional subsystems via configuration
- Nori agent proxy library for SDK abstraction
- Game integration concept (interact with assistant within a game world)
- Git-friendly database storage replacing opaque binary SQLite diffs with diffable representations

---

**Project name**: "Tachikoma" (タチコマ) - From Ghost in the Shell. Think-tanks that are connected to everything, curious, proactive, and develop unique personalities through accumulated experience. Represents the vision of an assistant that is connected, learns, and takes initiative.

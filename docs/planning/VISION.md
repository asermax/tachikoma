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

### v1 — Pending

**Critical:**
- Auto-close idle sessions so post-processing triggers without requiring a topic shift
- Summarize agent actions instead of generic tool markers in Telegram responses
- Telegram push notifications so users know when the agent has responded or a task completed
- Hot-reload skills so newly authored or modified skills are available without restart

**High:**
- Run as a persistent background service that starts on boot and restarts on failure
- Built-in skill authoring guide that teaches the agent how to scaffold new skills

**Medium:**
- Package as installable CLI tool via uv for easy setup and updates
- Granular processing status messages replacing the generic "Thinking..." indicator
- Receive images and audio from Telegram for multimodal processing

**Low / Backlog:**
- LLM observability for tracking token usage, latency, and costs across all agent interactions
- Base evaluation framework for testing agent processing pipelines
- Semantic similarity search for memory retrieval (embedding-based, replacing keyword search)

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
- Text-based conversation (no voice, no rich UI for v1)

**Agent Framework:**
- Claude Agent SDK for agent orchestration
- Per-message SDK client with conversation continuity via session resumption
- Session forking for post-processing and background task execution
- Skills system for specialized agent delegation

**Workspace:**
- A git-managed directory containing all persistent data (memories, core context files, skill definitions, configuration)
- Changes committed automatically for version history and rollback
- Markdown files for memories, with agent-driven search for retrieval
- SQLAlchemy with aiosqlite for session and task tracking; Alembic for migrations

**External Systems:**
- Telegram Bot API (communication)
- Anthropic API via Claude Agent SDK (agent execution)
- Local filesystem (git-managed workspace)

**Configuration:**
- Environment variables for API keys (Telegram, Anthropic)
- TOML config file with Pydantic validation and auto-generated defaults for tunable parameters

## Success Criteria

### Achieved

1. Contextual conversations via Telegram and REPL that reference past interactions through automatic memory retrieval
2. Automatic memory extraction (episodic, facts, preferences) after conversations close, surfaced in future sessions
3. Skill-based specialization with LLM detection and specialized agent delegation
4. Proactive task scheduling with cron-based and one-shot tasks, session-aware and background execution modes
5. Multi-project management with automatic synchronization, context injection, and commit/push on session close
6. Git-versioned workspace with automatic commits after every session
7. Conversation boundary detection with topic shift analysis and session resumption

### Still Needed for v1

1. Auto-close idle sessions so post-processing triggers reliably without requiring a topic shift
2. Telegram push notifications so users are alerted to responses and task completions
3. Hot-reload skills so the agent can author and use new skills in the same session
4. Readable action summaries in Telegram responses instead of generic tool markers
5. Persistent background service packaged as an installable CLI tool

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

**Advanced Proactivity:**
- Event-driven triggers from external sources (new emails, calendar events)
- Pattern detection and insight surfacing
- Dynamic profile building

**Other:**
- Feature toggles for disabling optional subsystems via configuration
- Nori agent proxy library for SDK abstraction
- Game integration concept (interact with assistant within a game world)

---

**Project name**: "Tachikoma" (タチコマ) - From Ghost in the Shell. Think-tanks that are connected to everything, curious, proactive, and develop unique personalities through accumulated experience. Represents the vision of an assistant that is connected, learns, and takes initiative.

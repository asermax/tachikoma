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
- **Obsidian + Claude Code (current workflow)**: Works well for vault management but lacks persistent memory, proactive behavior, and always-on availability

**What's needed:**
An opinionated personal assistant built on Claude Code SDK that uses a delegation-first architecture, maintains persistent memory across conversations, and proactively processes tasks during idle time — all accessible through a simple chat interface.

## Core Workflows

### 1. Contextual Conversation

**Trigger**: User sends a message via Telegram
**Steps**:
1. Pre-processing pipeline receives the incoming message
2. Context providers run in parallel: memory provider retrieves relevant past context, skills provider detects applicable skills
3. Enriched message (original + injected context) is passed to the coordinator agent
4. Coordinator handles the request directly or delegates to a specialized sub-agent
5. Response sent back to the user via Telegram

**Result**: Every conversation benefits from the assistant's accumulated knowledge about the user

### 2. Memory Extraction

**Trigger**: Conversation boundary detected (topic change on new message, or inactivity timeout as fallback)
**Steps**:
1. Boundary detection closes the current session in the session registry — either via topic analysis on an incoming message or inactivity timeout when the user goes silent
2. Session closure triggers the post-processing pipeline with the completed conversation
3. Extracts new facts, decisions, preferences, and patterns
4. Stores memories as written documents (not embeddings or key-value pairs), linked back to the source session
5. Memories are available for pre-processing retrieval in future conversations

**Result**: The assistant learns from every interaction without explicit user action

### 3. Proactive Task Processing

**Trigger**: Session is idle (no active conversation)
**Steps**:
1. Tasks accumulate in a queue during conversations (e.g., "remind user about X later", "process new emails", "summarize today's notes")
2. When session is idle, queue processor picks up the next task
3. Task executes without interrupting the user (no mid-conversation notifications)
4. Results are stored or queued for the next interaction
5. If a task produces something the user should see, it's delivered at the start of the next conversation

**Result**: The assistant works in the background, surfacing results when appropriate

### 4. Delegated Task Execution

**Trigger**: Coordinator agent determines a request needs specialized handling
**Steps**:
1. Coordinator analyzes the request and selects the appropriate sub-agent
2. Sub-agent receives only the context relevant to its task (avoids context poisoning)
3. Sub-agent executes (e.g., vault search, calendar check, email triage)
4. Result flows back to the coordinator
5. Coordinator synthesizes and responds to the user

**Result**: Complex requests are handled by focused agents that stay sharp and maintainable

## Scope

### v1 Requirements

**Agent Core:**
- Coordinator agent built on Claude Code SDK
- Telegram bot as the primary communication channel
- Basic delegation: coordinator can spawn sub-agents for focused tasks
- Conversation boundary detection via topic analysis (primary) and configurable inactivity timeout (~20 minutes default, fallback)
- Session tracking: registry of conversation sessions with IDs, timestamps, and conversation file references for post-processing and history
- Core context files: SOUL.md (personality/tone), USER.md (user information), AGENTS.md (agent instructions) — loaded with higher priority than dynamic memory
- Installable as a CLI tool via uv for easy setup and updates
- First-run workspace initialization: creates required directory structure and default core context files
- Git-managed workspace: all persistent files (memories, core context, skills, configuration) live in a versioned git repository with automatic commits for history and rollback

**Pre/Post Processing Pipeline:**
- Pre-processing: inject relevant memories before the agent sees a message
- Post-processing: extract facts, decisions, and preferences after a conversation ends
- Context providers as pluggable agents (memory provider and skills provider for v1, extensible for more)

**Skills System:**
- Skills defined as markdown documents (workflows or knowledge that any agent can load)
- Skill registry for managing available skills
- Skills provider detects relevant skills during pre-processing and injects them into agent context

**Memory System:**
- Store memories as written documents (markdown files)
- Retrieve relevant memories during pre-processing via semantic search
- Simple memory types: facts, preferences, decisions, patterns
- Time-based relevance (recent memories weighted higher)

**Proactive Behavior:**
- Queue-based idle processing (tasks execute when session is idle)
- Tasks can be queued during conversations or by external triggers
- No cron-based interruptions — process only during downtime

**Observability:**
- Structured logging for debugging agent behavior in production
- Event tracking for key agent actions (delegations, memory operations, task processing)
- LLM observability for agent interactions (inputs, outputs, token usage, latency, costs) using local/self-hosted tooling

**Quality Assurance:**
- Base evaluation framework for testing agent processing pipelines
- Eval suites for critical pipelines (context processing, memory extraction, core context updates)

### Not Now

Explicitly out of scope for v1:

**Advanced Memory:**
- Async session evaluation and pattern consolidation (ACE Reflector/Curator cycle)
- Embedding on questions for retrieval (vs. answers)
- Contradiction detection and resolution
- Memory decay and archival

**Context Providers (beyond memory):**
- Calendar provider (Google Calendar)
- Tasks provider (Google Tasks)
- Email provider (Gmail)
- Notes provider (Obsidian vault)
- Dynamic/user-created providers

**Constrained Workflows:**
- Deterministic step-by-step skill execution harness
- Non-LLM workflow state management
- Workflow declarations with step dependencies

**Workflow Guardrails:**
- Planner agent generates execution plan before workflow runs
- Evaluator agent monitors step outputs and collects friction metadata
- Feeds into optimization and self-improvement cycles

**Advanced Workspace Management:**
- Two-tier change model: direct commits for data, branch + PR for behavior changes
- Conflict resolution for concurrent workspace modifications
- Skill self-optimization via execution traces, friction data, and git branches

**Interfaces:**
- Web interface (chat + dashboard)
- Hardware form factor (speaker with display)

**Advanced Proactivity:**
- Event-driven triggers from external sources (new emails, calendar events)
- Pattern detection and insight surfacing
- Dynamic profile building

## Technical Context

**Platform:**
- Linux (primary development target)
- Runs as a persistent background service
- Single-user, self-hosted

**Language/Runtime:**
- Python (Claude Code SDK has a Python SDK)
- Installable as a CLI tool via uv

**User Interaction:**
- Telegram Bot API as primary interface
- Text-based conversation (no voice, no rich UI for v1)

**Agent Framework:**
- Claude Code SDK for agent orchestration
- Coordinator + sub-agent pattern
- Each agent gets scoped context and tools

**Workspace:**
- A git-managed directory containing all persistent data (memories, core context files, skill definitions, configuration)
- Changes committed automatically for version history and rollback
- Markdown files for memories with semantic search for retrieval (embedding model TBD)
- File-based — no database for v1

**External Systems:**
- Telegram Bot API (communication)
- Anthropic API via Claude Code SDK (agent execution)
- Local filesystem (git-managed workspace)

**Configuration:**
- Environment variables for API keys (Telegram, Anthropic)
- Config file for tunable parameters (inactivity threshold, memory directory, etc.)

## Success Criteria

v1 is successful when:
1. Can hold a conversation via Telegram that feels contextually aware (references past interactions)
2. Memories are extracted automatically after conversations end and surface in future ones
3. The coordinator delegates at least one task type to a sub-agent (e.g., memory retrieval)
4. Proactive tasks queue up and execute during idle time without interrupting active conversations
5. The system runs as a persistent service that survives restarts (reconnects to Telegram, preserves memory)

## Future Considerations

Ideas for v2 and beyond (not committing to these):
- ACE Framework cycle: async session evaluation, pattern consolidation, memory curation
- Context providers for calendar, tasks, email, vault (each as a pluggable agent)
- Constrained workflow execution with guardrails (planner + evaluator agents, friction-based optimization)
- Skill self-optimization: background analysis of execution traces proposes improvements via workspace branches
- Web interface with chat + dashboard
- Nori agent proxy library for SDK abstraction (swap Claude Code SDK for alternatives per task)
- Event-driven triggers from external sources
- Hardware presence (speaker with simple display, like Towano no Yuugure)
- Game integration concept (interact with assistant within a game world)

---

**Project name**: "Tachikoma" (タチコマ) - From Ghost in the Shell. Think-tanks that are connected to everything, curious, proactive, and develop unique personalities through accumulated experience. Represents the vision of an assistant that is connected, learns, and takes initiative.

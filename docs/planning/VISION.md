# Project Vision: Tachikoma

**A proactive personal assistant that remembers, learns, and takes initiative.**

*Not just responding to commands — anticipating needs, managing context, and evolving through use.*

Tachikoma is built in TypeScript on the pi agent SDK. The architecture sections describe the stack: a thin core shell where every feature ships as an extension.

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
- **Subprocess-based agent SDKs (e.g. Claude Agent SDK)**: Capable runtimes, but the per-message subprocess model forces significant accidental complexity (session resume gymnastics, context persistence and reassembly, custom transports) and keeps the agent runtime at arm's length from the host

**What's needed:**
An opinionated personal assistant built on an embeddable agent SDK that maintains conversation continuity across messages, enriches every interaction with accumulated context (memories, skills, project state), and autonomously processes tasks on schedules — all accessible through a simple chat interface. Tachikoma builds that machinery on pi: long-lived in-process sessions, a native extension system, and TypeScript end to end.

## Core Workflows

### 1. Contextual Conversation

**Trigger**: User sends a message via Telegram or the terminal REPL
**Steps**:
1. On a new conversation, the pre-processing pipeline enriches the session with context gathered in parallel: the memory provider surfaces relevant past interactions and the projects provider injects current project state — each contributed by its extension
2. The coordinator prompts a long-lived pi `AgentSession` — one in-process session per conversation, so continuity is the default rather than something reconstructed per message
3. Skills are available through pi's progressive disclosure: skill descriptions live in the system prompt and the agent reads full instructions on demand when the topic calls for them
4. Session events stream back through the coordinator as domain events, and the active channel renders them to the user

**Result**: Every conversation benefits from the assistant's accumulated knowledge about the user, relevant skills, and project context

### 2. Memory Extraction

**Trigger**: Conversation boundary detected (topic shift on a new message, or idle timeout)
**Steps**:
1. On each incoming message, boundary detection analyzes whether the user is continuing the current topic or shifting to a new one; if a shift is detected, it also checks whether the new topic matches a recent past conversation that can be resumed
2. Session closure triggers the post-processing pipeline with the completed conversation — processors read the session's JSONL transcript and run one-shot extraction via side-channel LLM calls
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
2. **Session tasks** execute during the user's active session when idle — gated by a configurable idle period so they never interrupt active conversation. Prompts are injected into the live session and results are delivered as proactive messages through the active channel
3. **Background tasks** execute autonomously in their own in-memory pi sessions, independent of whether the user is chatting. They run iteratively until the work is assessed as complete, and produce notification messages summarizing what was done
4. Task schedules support timezone-aware cron expressions and survive restarts with catch-up for missed runs

**Result**: The assistant works autonomously on scheduled and ad-hoc tasks, delivering results as notifications or proactive messages at appropriate times

### 5. Skill-based Specialization

**Trigger**: User's message matches a domain where specialized knowledge is available
**Steps**:
1. Skills are packages following the Agent Skills standard — a `SKILL.md` with YAML frontmatter whose description advertises when the skill applies
2. pi surfaces every skill's description in the system prompt (progressive disclosure); when the conversation enters a skill's domain, the agent reads the full instructions on demand — no separate classification pass
3. Loaded skills can carry references, scripts, and bundled agent definitions the assistant delegates focused work to
4. The workspace skills directory is a first-class skill source, and changes to it are picked up without restarting

**Result**: The assistant adapts its capabilities to the topic at hand, drawing on packaged expertise as needed — with detection handled natively by the agent runtime instead of a bolt-on classifier

## Architecture

### Thin Core

The core is deliberately small — a shell that hosts extensions. It owns exactly:

- **Configuration**: TOML at `~/.config/tachikoma/config.toml`, TypeBox-validated, auto-generated defaults
- **Logging**: structured pino logging with per-component child loggers
- **Database**: drizzle over `node:sqlite` at `{workspace}/.tachikoma/tachikoma.db`, with migrations
- **Typed event bus**: decoupled cross-extension communication
- **Scheduler**: croner-based in-process cron and one-shot scheduling
- **Channel registry**: pluggable user-facing surfaces (REPL, Telegram)
- **Coordinator**: the main message loop — enqueue, boundary handling, prompting the pi session, streaming domain events to channels
- **Session registry**: database-backed tracking of conversation lifecycle, transcripts, and summaries
- **Extension host**: loads extensions and hands them the `AppContext`

The core contains no features. It does not know what memory, skills, or tasks are.

### Everything Else Is an Extension

Every feature ships as a Tachikoma extension in a single format:

```ts
defineExtension({ name, config?, setup(app) })
```

`setup` receives an `AppContext` exposing app-level hooks — scheduler, database, session lifecycle, channels, context provider registration, post-processing registration — and can contribute pi extension factories via `app.agent.use((pi) => ...)` for in-session behavior: tools (`pi.registerTool`), event hooks, and system prompt fragments.

First-party extensions, all in-repo: **context** (SOUL.md/USER.md/AGENTS.md), **memory**, **boundary** (topic-shift detection, rolling summaries, session resumption), **skills**, **workflows**, **tasks**, **projects**, **git** (workspace versioning), **telegram**, **repl**, **detached-processes**, **notifications**, and **external** (loading and installing out-of-tree extensions built on the same extension contract).

### Sessions on pi

pi sessions are long-lived in-process objects: the coordinator holds one `AgentSession` per conversation and replaces it on topic boundary via `AgentSessionRuntime` — no per-message client recreation, no resume bookkeeping, no context persistence layer to reassemble injected context. Transcripts are tree-structured JSONL files under a dedicated `agentDir` (`{workspace}/.tachikoma/pi`), isolating Tachikoma's pi state from any user pi install.

Post-session extraction opens the JSONL transcript read-only and runs one-shot side-channel `complete()` calls from pi-ai. Agent tools are plain `pi.registerTool()` registrations, and skill discovery relies on pi's native progressive disclosure rather than an LLM classification pass.

### Workspace Compatibility

The workspace data contract is stable: markdown memories organized by type, SOUL.md/USER.md/AGENTS.md core context files, Agent Skills-format skill packages, git versioning. Pointing an existing `~/tachikoma` workspace at a fresh install must just work. Configuration and the database are implementation artifacts, not user data — legacy formats are detected and adapted at startup.

## Scope

### v1 — Delivered Scope

A thin core shell plus first-party extensions covering the full assistant feature set (see [DELTAS.md](DELTAS.md) for open work):

**Core shell + walking skeleton:**
- Toolchain (Node type stripping, pnpm, Biome, vitest, just)
- Configuration, logging, database, typed event bus, scheduler
- Extension host with the `defineExtension`/`AppContext` contract
- Coordinator with a long-lived pi session, channel registry, and REPL channel — a user can hold a conversation end to end

**Sessions, boundary, memory, context:**
- Database-backed session registry with lifecycle, rolling summaries, idle timeout
- Boundary detection with topic-shift analysis, session replacement, and resumption (including cold-start)
- Pre/post-processing pipelines with parallel execution and error isolation
- Memory extraction (episodic, facts, preferences), memory context retrieval, and git-versioned transcript archiving with age-based retention
- Core context files injected into the system prompt, with post-session updates

**Skills, workflows, tasks:**
- Skills via pi's native Agent Skills support with the workspace as a skill source, hot-reload, bundled agents, and the authoring guide
- Workflow engine with directory-based definitions, persisted step state machine, and lifecycle tools
- Task system: cron and one-shot definitions, management tools, session (idle-gated) and background (autonomous) execution

**Channels, projects, git:**
- Full Telegram channel: streamed rendering, complete media support, push notifications
- Projects extension: registration tools, startup sync, context injection, commit/push on close
- Git extension: workspace versioning after each session and fetch-rebase-push sync

**Detached processes, notifications, external extensions, polish:**
- Detached process supervision tools
- Event-bus-driven notifications with buffered, idle-aware delivery
- External extension loading and git-based installation (out-of-tree extensibility is the unified extension API itself — no separate plugin system)
- Granular processing status updates and the release pipeline

### Out of Scope

Machinery that subprocess-based agent SDKs force on a host and pi makes unnecessary is deliberately absent:
- Custom SDK transports (ARG_MAX tempfile workarounds) — pi is embedded in-process
- Per-message client recreation, context persistence entries, and context reassembly — sessions are long-lived
- LLM-based skill classification — pi's progressive disclosure covers detection
- Subprocess stderr capture — there is no subprocess

Larger capabilities (parallel sessions, autonomous long-running agents, proactive nudges, evaluation framework, observability) are tracked as backlog deltas — see Future Considerations.

### Beyond v1

The long-term backlog builds on this foundation — see Future Considerations and [DELTAS.md](DELTAS.md).

## Technical Context

**Platform:**
- Linux (primary development target)
- Runs as a persistent background service
- Single-user, self-hosted

**Language/Runtime:**
- TypeScript on Node.js >= 22.19 with native type stripping — no build step in development
- pnpm for dependency management; Biome for lint/format; vitest for tests; just as task runner

**Agent Framework:**
- pi agent SDK (`@earendil-works/pi-coding-agent`, pinned exact) embedded via `createAgentSession`
- One long-lived `AgentSession` per conversation; `AgentSessionRuntime` for boundary-driven replacement
- pi extension API for tools, event hooks, and system prompt contributions; `pi-ai` for side-channel completions (boundary detection, summaries, extraction)
- Agent Skills standard for skill packages with progressive disclosure
- Tree-structured JSONL transcripts with fork support under the workspace `agentDir`

**User Interaction:**
- Telegram Bot API (via grammY) as primary interface — text, images, audio, voice, documents, stickers, video, and animations
- Terminal REPL for local development and direct interaction

**Workspace:**
- A git-managed directory containing all persistent user data (memories, core context files, skill definitions)
- Markdown files for memories, with retrieval during pre-processing
- drizzle over `node:sqlite` for session, task, and workflow tracking; drizzle-kit for migrations
- TOML configuration validated with TypeBox, auto-generated defaults on first run

**External Systems:**
- Telegram Bot API (communication)
- LLM providers via pi's multi-provider model registry (Anthropic primary)
- Local filesystem (git-managed workspace)

## Success Criteria

1. Walking skeleton: a REPL conversation flows end to end through the thin core and a long-lived pi session
2. Conversations close on topic shift or idle timeout, and memory extraction (episodic, facts, preferences) plus core context updates run automatically
3. Past conversations resume on topic match, including across process restarts
4. A pre-existing workspace (memories, context files, skills) works unmodified
5. Skills are detected and applied via progressive disclosure without a classification pass, and new skills are available without restart
6. Scheduled tasks run in both session (idle-gated) and background (autonomous, iterative) modes with timezone-aware schedules and restart catch-up
7. Telegram is a full-fidelity channel: streamed responses, full media support, push notifications
8. Registered projects sync on startup and auto-commit/push on session close; the workspace itself is git-versioned after every session
9. Detached processes can be dispatched and supervised; notifications buffer and deliver at conversation pauses; third-party extensions load via the external extension loader
10. Every feature outside the core shell is implemented as an extension using the single `defineExtension` format

## Future Considerations

Ideas beyond the delivered scope (not committing to these):

**Long-standing backlog:**
- Parallel conversation sessions
- Delegation to autonomous long-running agents
- Proactive nudges from past conversations (sensor framework, fatigue management)
- Agent-driven episodic memory search tools
- LLM observability and the evaluation framework for agent pipelines
- Structured error classification and surfacing across pipelines
- Secrets store and secure credential delivery to whitelisted commands

**Advanced Memory:**
- ACE Framework cycle: async session evaluation, pattern consolidation, memory curation
- Embedding-based semantic similarity search for retrieval
- Contradiction detection and resolution; memory decay and archival

**Context Providers (beyond memory):**
- Calendar, tasks, email, and notes providers
- Dynamic/user-created providers via externally installed extensions

**Channels and Interfaces:**
- Web interface with chat and dashboard
- Hardware presence (speaker with simple display)

**Operational Tooling:**
- CLI for querying internal state without starting a conversation
- Transcript search and analysis tools
- External command processor for remote management

---

**Project name**: "Tachikoma" (タチコマ) - From Ghost in the Shell. Think-tanks that are connected to everything, curious, proactive, and develop unique personalities through accumulated experience. Represents the vision of an assistant that is connected, learns, and takes initiative.

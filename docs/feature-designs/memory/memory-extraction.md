# Design: Memory Extraction

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/memory/memory-extraction.md](../../feature-specs/memory/memory-extraction.md)
**Status**: Current

## Purpose

This document explains the design rationale for memory extraction: how memory processors fork SDK sessions to extract memories, and how the bootstrap hook initializes the memory directory structure.

For the post-processing pipeline infrastructure that memory processors plug into, see the [post-processing pipeline design](../agent/post-processing-pipeline.md).

## Problem Context

Conversations are ephemeral — once a session ends, the context is lost. The assistant needs a way to automatically extract and persist learnings so that future sessions can reference past interactions, known user information, and expressed preferences.

**Constraints:**
- Memory extraction happens after a conversation ends — it must not block the user or the shutdown flow
- The SDK's standalone `query()` function is the mechanism for session forking — it operates independently of the coordinator's `ClaudeSDKClient`
- All file I/O is performed by the forked LLM agent, not by processor code — processors are thin orchestration wrappers
- Memories are plain markdown files in the workspace — no database, human-readable and directly editable

**Interactions:**
- Coordinator (core-architecture): triggers pipeline on session close in `__aexit__`
- Post-processing pipeline: memory processors register in the default `main` phase (see [pipeline design](../agent/post-processing-pipeline.md))
- Sessions: provides the `Session` dataclass with `sdk_session_id` for forking
- Workspace bootstrap: memory hook creates directory structure

## Design Overview

Three **memory processors** extend `PromptDrivenProcessor` (DES-004) and plug into the [post-processing pipeline](../agent/post-processing-pipeline.md), registering in the default `main` phase and running in parallel.

```
┌───────────────────────────────────────────────────────────┐
│                       __main__.py                         │
│                                                           │
│  pipeline = PostProcessingPipeline()                      │
│  pipeline.register(EpisodicProcessor(cwd))                │
│  pipeline.register(FactsProcessor(cwd))                   │
│  pipeline.register(PreferencesProcessor(cwd))             │
│  pipeline.register(CoreContextProcessor(cwd))             │
│                                                           │
│  Coordinator(..., pipeline=pipeline)                      │
└───────────────────────────────────────────────────────────┘
                          │
           ┌──────────────┼──────────────┬──────────────┐
           ▼              ▼              ▼              ▼
      ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌──────────┐
      │Episodic │   │  Facts  │   │  Prefs  │   │  Context │
      │Processor│   │Processor│   │Processor│   │ Processor│
      └────┬────┘   └────┬────┘   └────┬────┘   └────┬─────┘
           │              │              │              │
           ▼              ▼              ▼              ▼
      fork_and_consume(prompt, cwd)                fork_and_consume(
           │              │              │            prompt, cwd,
           ▼              ▼              ▼            mcp_servers=...)
      memories/      memories/      memories/           │
      episodic/      facts/         preferences/        ▼
                                                   context/ files
```

Each **memory processor** is a `PromptDrivenProcessor` subclass (DES-004, scoped writer tier) that provides an extraction prompt. Prompts use `$WORKSPACE` placeholders for file paths (DES-008), which the base class replaces with the absolute workspace path at construction time — ensuring forked agents use absolute paths regardless of the CLI's session-restored working directory. The base class handles forking the SDK session via `fork_and_consume()`. Forked agents have `Read`, `Glob`, `Grep`, `Bash`, `Edit`, and `Write` tools, path-scoped to their memory subdirectory, with Bash gated to utility-only commands via `UTILITY_BASH_HOOK` (DES-004). The forked agent autonomously reads, creates, updates, or deletes memory files — the processor code performs no file I/O.

For the context update processor that also runs in the main phase, see [core-context-updates design](../agent/core-context-updates.md).

The memory package also owns `TranscriptArchiveProcessor` — a plain `PostProcessor` (not `PromptDrivenProcessor`) that copies the SDK-owned transcript into `memories/transcripts/<sdk-session-id>.jsonl` on session close. It is deterministic I/O with no LLM reasoning, registered in the `pre_finalize` phase so it runs after the extraction processors have read the SDK transcript and before the git commit processor stages the workspace. See the [post-processing pipeline design](../agent/post-processing-pipeline.md) for phase semantics.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/memory/__init__.py` | Re-exports: `EpisodicProcessor`, `FactsProcessor`, `PreferencesProcessor`, `TranscriptArchiveProcessor`, `memory_hook` | Clean public API for the memory package |
| `src/tachikoma/memory/hooks.py` | `memory_hook`: creates `memories/` directory structure — `episodic/`, `facts/`, `preferences/`, and `transcripts/` subdirectories | Subsystem-owned hook pattern; registered after context hook; `transcripts/` lives alongside the extraction subdirectories because it is owned by the same package, even though it is populated by a deterministic processor |
| `src/tachikoma/memory/episodic.py` | `EpisodicProcessor(PromptDrivenProcessor)` + `EPISODIC_PROMPT` constant | Extends DES-004 base class; prompt co-located with processor |
| `src/tachikoma/memory/facts.py` | `FactsProcessor(PromptDrivenProcessor)` + `FACTS_PROMPT` constant | Extends DES-004 base class; prompt co-located with processor |
| `src/tachikoma/memory/preferences.py` | `PreferencesProcessor(PromptDrivenProcessor)` + `PREFERENCES_PROMPT` constant | Extends DES-004 base class; prompt co-located with processor |
| `src/tachikoma/memory/transcripts.py` | `TranscriptArchiveProcessor(PostProcessor)` — copies `session.transcript_path` to `memories/transcripts/<sdk-session-id>.jsonl` via `shutil.copy2` | Plain `PostProcessor` (no sub-agent — deterministic I/O); self-healing `mkdir(parents=True, exist_ok=True)` inside `process()`; all errors (`FileNotFoundError`, `OSError`) logged and swallowed so the pipeline never crashes on archival failure |

### Cross-Layer Contracts

```mermaid
sequenceDiagram
    participant Pipeline as PostProcessingPipeline
    participant Proc as MemoryProcessor
    participant SDK as query()
    participant FS as Workspace Files

    rect rgba(0, 128, 255, 0.1)
        Note over Pipeline,FS: Phase: main (parallel execution)
        Pipeline->>Proc: process(session) [x3 in parallel]
        Proc->>SDK: fork_and_consume(session, prompt, cwd)
        SDK->>FS: agent reads/writes memory files
        SDK-->>Proc: async iterator consumed
        Proc-->>Pipeline: complete (or exception)
    end
```

**Integration Points:**
- Processors ↔ Pipeline: memory processors register in the default `main` phase (see [pipeline design](../agent/post-processing-pipeline.md))
- Processors ↔ SDK: `fork_and_consume` calls `query(prompt, options=ClaudeAgentOptions(cwd=cwd, resume=session.sdk_session_id, fork_session=True, permission_mode="bypassPermissions"))` — standalone function, independent of `ClaudeSDKClient`
- Forked agents ↔ Workspace: agents read/write markdown files in `memories/` subdirectories
- Bootstrap ↔ Memory hook: `memory_hook` creates directory structure on startup

## Modeling

The domain model is minimal — no persistent entities or database tables. Memory files are unstructured markdown managed by forked LLM agents.

```
EpisodicProcessor(PromptDrivenProcessor)    [DES-004]
└── EPISODIC_PROMPT: str

FactsProcessor(PromptDrivenProcessor)       [DES-004]
└── FACTS_PROMPT: str

PreferencesProcessor(PromptDrivenProcessor) [DES-004]
└── PREFERENCES_PROMPT: str
```

Each processor inherits `_prompt`, `_cwd`, and the default `process()` implementation from `PromptDrivenProcessor`. All three pass `model=agent_defaults.processor_model` at construction so their forks run on the configured mechanical-work model tier (default `"haiku"`). Mechanical extraction doesn't benefit from higher-tier reasoning, and smaller requests are less likely to trip upstream rate limits when the pipeline fires multiple forks concurrently on session close. The `processor_model` setting is shared across all post-processors (see DES-004 for the role taxonomy). For the base class, `PostProcessingPipeline`, `PostProcessor` ABC, and `fork_and_consume` models, see the [pipeline design](../agent/post-processing-pipeline.md).

## Data Flow

### Memory processor flow (per processor)

```
1. processor.process(session) is called
2. Base class references the extraction prompt (set in constructor via DES-004 pattern)
3. Base class calls fork_and_consume(session, self._prompt, self._cwd):
   a. Creates ClaudeAgentOptions(cwd=self._cwd, resume=session.sdk_session_id, fork_session=True, permission_mode="bypassPermissions")
   b. Calls query(prompt=prompt, options=options)
   c. Async iterates over the returned generator to consume all messages
   d. The forked agent (LLM) autonomously:
      - Reads existing files in its memory subdirectory
      - Analyzes the conversation history (via the forked session)
      - Creates, updates, or deletes memory files as needed
4. Once the async iterator is exhausted, the forked session ends
```

### Transcript archival flow

```
1. TranscriptArchiveProcessor.process(session) runs in the pre_finalize phase
2. If session.transcript_path is None or session.sdk_session_id is None → debug log, return
3. dest_dir = cwd / "memories" / "transcripts"
4. dest = dest_dir / f"{session.sdk_session_id}.jsonl"
5. dest_dir.mkdir(parents=True, exist_ok=True)   — self-healing if removed after bootstrap
6. shutil.copy2(Path(session.transcript_path), dest)
   - FileNotFoundError → warning log, return
   - OSError → error log with traceback, return
7. On success: info log with session id and destination path
8. The finalize phase's GitProcessor picks up the new/updated file via its normal "stage everything in workspace" behavior
```

## Key Decisions

### Processor-per-file with co-located prompts

**Choice**: Each processor in its own file with extraction prompt as module-level constant. All three extend `PromptDrivenProcessor` (DES-004), inheriting the `process()` implementation.
**Why**: Co-locates related concerns. Each file is self-contained — just a prompt constant and a near-empty class. When iterating on extraction quality, developers modify one file per memory type.

**Consequences**:
- Pro: Self-contained files per processor
- Pro: Near-trivial subclasses thanks to DES-004 base class
- Con: Prompt changes require code changes (acceptable)

### Pipeline trigger timing — after session close, before SDK disconnect

**Choice**: Pipeline runs in `__aexit__` after `registry.close_session()` but before `client.disconnect()`.
**Why**: The pipeline uses standalone `query()` (not `ClaudeSDKClient`), so it doesn't depend on the client connection. Running before disconnect maintains clean ordering. The session must be closed first so the registry is in a consistent state.

**Consequences**:
- Pro: Clean ordering — session close → post-processing → SDK disconnect
- Pro: Pipeline independent of SDK client state
- Con: Adds latency to shutdown (acceptable — extraction runs in parallel)

### Plain `PostProcessor` for transcript archival (no sub-agent)

**Choice**: `TranscriptArchiveProcessor` extends `PostProcessor` directly — no forked sub-agent, just `shutil.copy2` with `loguru` logging.
**Why**: Copying a file has no reasoning component. Forking a sub-agent (DES-004 `PromptDrivenProcessor`) would add LLM latency, cost, and failure modes for deterministic I/O. DES-004 is scoped to sub-agent spawning; plain `PostProcessor` remains the right choice when the task is purely mechanical.

**Consequences**:
- Pro: No prompt/tool/permission configuration — processor module is stdlib-only
- Pro: Tests run with pure filesystem assertions (no LLM mocks)
- Pro: Establishes precedent (alongside `StaleWorkflowCleanupProcessor`) that deterministic processors live in the same pipeline as sub-agent processors without ceremony

### `pre_finalize` phase for transcript archival

**Choice**: Register `TranscriptArchiveProcessor` on the `pre_finalize` phase.
**Why**: (a) The `main` phase's memory processors read the SDK transcript — they must complete before we copy it, otherwise we race their reads. (b) The `finalize` phase is `GitProcessor`'s — the archive must already be on disk when git stages. `pre_finalize` is the only phase that satisfies both constraints.

**Consequences**:
- Pro: Archive is guaranteed present for the git commit without coordinating with `finalize` processors
- Pro: Runs concurrently with other `pre_finalize` processors (`ProjectsProcessor`, `StaleWorkflowCleanupProcessor`) — disjoint filesystem scopes, no conflict

### Archived path derived from `sdk_session_id`

**Choice**: The archived path is always `memories/transcripts/<sdk-session-id>.jsonl` — computed from the session's existing `sdk_session_id` field. No `archived_transcript_path` column is added.
**Why**: The SDK session ID is already globally unique, already stored on `Session`, and already filesystem-safe. Adding a column would duplicate information the system can compute.

**Consequences**:
- Pro: Zero migration cost
- Pro: Consumers compute the path with a one-line helper or inline `f-string`
- Con: A future scheme change (different filename, different location) becomes a code change rather than a data migration — acceptable given the single source of archival logic

### Defensive `mkdir` inside `process()` (self-healing)

**Choice**: The processor calls `dest_dir.mkdir(parents=True, exist_ok=True)` on every run in addition to the bootstrap hook creating the directory at startup.
**Why**: Handles the edge case where `memories/transcripts/` is removed between bootstrap and session close (manual cleanup, fresh clone, etc.). Cost is negligible and the "log and continue" error policy absorbs any mkdir failure.

**Consequences**:
- Pro: Processor survives directory deletion mid-run
- Pro: No special-case error-handling branch required for the missing-directory AC

## System Behavior

### Scenario: Normal shutdown with conversation history

**Given**: A conversation session with a valid `sdk_session_id`
**When**: The coordinator's `__aexit__` fires
**Then**: Session is closed. Pipeline runs all three memory processors in the main phase. Each forks the session and the forked agent reads/writes memory files. After completion, SDK client disconnects.

### Scenario: One processor fails

**Given**: Three processors running in parallel
**When**: One processor's `query()` call fails
**Then**: `asyncio.gather(return_exceptions=True)` captures the exception. Other processors complete normally. Pipeline logs the failure.

### Scenario: Trivial conversation

**Given**: Session closes with minimal content
**When**: Pipeline runs all processors
**Then**: Each forked agent determines there's nothing meaningful. No files created. Valid outcome.

### Scenario: Multiple conversations on the same day

**Given**: Two conversations close on the same date
**When**: Episodic processor runs for the second
**Then**: Agent consolidates entries for the day rather than creating duplicates.

### Scenario: User manually edits a memory file

**Given**: User edits `memories/facts/work-info.md`
**When**: Next facts processor runs
**Then**: Forked agent reads the user-edited file and respects changes.

### Scenario: Transcript archived on session close

**Given**: A closing session with populated `transcript_path` and `sdk_session_id`, and the SDK transcript file exists at that path
**When**: The post-processing pipeline advances through `pre_finalize`
**Then**: `TranscriptArchiveProcessor` copies the file to `memories/transcripts/<sdk-session-id>.jsonl`. The subsequent `finalize` phase's `GitProcessor` stages and commits the new file alongside the extracted memories.

### Scenario: SDK transcript already gone at archive time

**Given**: A closing session whose `transcript_path` points at a file removed before `pre_finalize` runs (e.g., `~/.claude` wiped between session end and post-processing)
**When**: `TranscriptArchiveProcessor.process(session)` runs
**Then**: `FileNotFoundError` is caught, a warning is logged with both source and destination paths, and the processor returns normally. Other `pre_finalize` processors and the `finalize` git commit are unaffected.

### Scenario: Filesystem error during archive

**Given**: The destination is read-only, disk is full, or any other `OSError` condition
**When**: `shutil.copy2` raises
**Then**: The error is logged with traceback, and the processor returns normally. The git commit still runs and commits whatever other workspace changes exist.

### Scenario: Archive directory missing at copy time

**Given**: `memories/transcripts/` was removed after bootstrap (manual cleanup, fresh clone)
**When**: The processor runs
**Then**: `mkdir(parents=True, exist_ok=True)` recreates it, then the copy proceeds. No error surfaces.

### Scenario: Idempotent re-archival

**Given**: A prior run already produced `memories/transcripts/<sid>.jsonl` (complete or partial)
**When**: The processor runs again for the same session
**Then**: `shutil.copy2` overwrites the destination. The final file matches the current source.

## Notes

- Forked sessions have no `max_turns` or `max_budget_usd` limits. Extraction prompts are focused, so sessions should be naturally short.
- Memory extraction quality is an LLM behavioral concern. Prompts are the primary quality lever.
- Forked sessions require `permission_mode="bypassPermissions"` to allow the extraction agent to read and write memory files without permission prompts.

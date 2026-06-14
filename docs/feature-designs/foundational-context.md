# Design: Foundational Context

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/foundational-context.md](../feature-specs/foundational-context.md)
**Status**: Current

## Purpose

Explains how the assistant's identity files (SOUL.md, USER.md, AGENTS.md) reach the pi system prompt and how the post-session updater that evolves them is structured.

## Problem Context

pi ships a coding-agent system prompt; Tachikoma needs a personal-assistant identity sourced from user-editable workspace files. Separately, those files must evolve from conversations without becoming noisy — one-off remarks must not rewrite the assistant's personality, but recurring patterns should.

**Constraints:**
- `app.agent.systemPrompt` builders are synchronous (`() => string`), so only synchronous file I/O (`readFileSync`) can run at composition time — no `await`ed reads
- pi already discovers AGENTS.md from the workspace root; duplicating it in the override would inject it twice
- The fork is tool-limited by a plain allowlist and there is no MCP — any agent-managed state must be a file (see `docs/reference/pi-sdk-notes.md`)

**Interactions:**
- The system prompt override is composed per session open by `AgentManager` (see [agent-integration](./agent-integration.md))
- The `core-context` processor runs in the post-processing pipeline owned by the coordinator (see [conversation-loop](./conversation-loop.md)) and is currently registered by the memory extension (see [memory](./memory.md))
- The periodic cleanup of these same three files (`runContextMaintenanceTick`, `memory-context-maintenance` cron) lives in the memory extension's maintenance module, not here — it complements the per-session updater by consolidating and pruning across the whole file rather than reacting to one conversation (see [memory](./memory.md))
- Workspace internals (`dataDir`) come from the core shell (see [core-shell](./core-shell.md))
- Context file changes are committed by the finalize-phase git processor (see [git-workspace](./git-workspace.md))

## Design Overview

Two halves with different lifetimes. The extension (`src/extensions/context/index.ts`) is process-scoped: a bootstrap hook creates SOUL.md and USER.md from templates on first run and caches their contents as a fallback, and a registered system-prompt builder re-reads both files from disk synchronously on every build (`readFileSync`, falling back to the cached snapshot only on a read error) and calls the core `buildMainSystemPrompt` ([DES-005](../design/DES-005-base-prompt-ownership.md)), which combines the identity, the shared `OPERATIONAL_GUIDANCE` plus interactive guidance, the file contents, and the workspace root. The updater (`src/extensions/context/processor.ts`) is a `preFinalize` post-processor: it expires and snapshots the pending-signals file, builds a follow-up instruction, then forks the just-ended pi session (`forkAndContinue`) so the same assistant — full conversation live in its history — edits the three context files and the signals file directly, hard-limited to file tools.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/context/index.ts` | Extension wiring: `load-context-files` bootstrap hook, `readOrCreate` with templates, system prompt builder that re-reads SOUL.md/USER.md from disk per build (`fresh()` with cached fallback) and delegates composition to core `buildMainSystemPrompt` | Only ENOENT triggers template creation — other read errors abort startup; per-build `readFileSync` so processor edits apply next session; AGENTS.md deliberately untouched (pi native discovery); base-prompt text lives in core, not inline ([DES-005](../design/DES-005-base-prompt-ownership.md)) |
| `src/extensions/context/processor.ts` | `createCoreContextProcessor` (`core-context`, `preFinalize`); pending-signals parse/serialize/clean; the full update policy as `INSTRUCTION_TEMPLATE` (follow-up user-instruction shape, with a silent-background directive) | Forks via `forkAndContinue` with the `MEMORY_FILE_TOOLS` allowlist; reuses `localIsoDate` from the memory extension; signals live in `dataDir`, outside user-visible workspace content |

## Key Decisions

### System prompt override instead of context injection

**Choice**: Register SOUL.md/USER.md through `app.agent.systemPrompt`, which feeds pi's `systemPromptOverride` (DES-001), rather than injecting them as per-message context blocks.
**Why**: Identity is system-prompt material — it must frame every turn, not arrive as a tagged user-visible context message; replacing pi's coding prompt is also the only way to stop being a coding agent.
**Alternatives Considered**:
- `app.agent.provideContext`: per-message tagged blocks are designed for volatile context (memory indexes, project state), not standing identity
- Writing into AGENTS.md: conflates personality and user knowledge with operational instructions, and loses the template/bootstrap story

**Consequences**:
- Pro: identity is uniform across sessions and invisible to per-message pipelines
- Pro: AGENTS.md keeps its native pi semantics (re-read on every session open) with zero code
- Pro: the synchronous builder re-reads SOUL.md/USER.md from disk per build, so edits (including the processor's own) apply on the next session without a restart; the bootstrap snapshot is only the read-failure fallback

### File-based pending signals edited by the agent

**Choice**: Store ambiguous signals as a dated markdown list at `{dataDir}/pending-signals.md`; the agent stages/promotes/discards by editing the file directly with its file tools, while the host only parses it for the prompt snapshot and enforces the 30-day expiry.
**Why**: The fork runs under a plain file-tool allowlist with no custom-tool transport, so the legacy implementation's dedicated MCP tools (`add_pending_signal`/`remove_pending_signal`) have nowhere to live — and they are unnecessary: a strict line format (`- **YYYY-MM-DD**: text`) lets the agent stage/promote/discard with the same `edit`/`write` tools it already uses for the context files. This is what unblocked the fork conversion: the pending-signals mechanism was already file-based, so no MCP porting was needed.
**Alternatives Considered**:
- Legacy MCP `add_pending_signal`/`remove_pending_signal` tools on the fork: pi has no MCP layer, and file edits cover the same need
- Database-backed signals: loses direct user inspectability and requires injection plumbing both ways

**Consequences**:
- Pro: one mechanism (file edits) for both context files and signal management; trivially inspectable
- Pro: host-side expiry is a pure function over the file, tested in isolation (`cleanPendingSignals`)
- Con: no all-or-nothing batch removal semantics — a sloppy edit can corrupt entries (unparseable content is detected and logged, not repaired)

### preFinalize phase, registered by the memory extension

**Choice**: The processor runs in the `preFinalize` phase and is registered in `src/extensions/memory/index.ts` next to the extraction processors.
**Why**: Phase ordering guarantees context updates land after memory extraction (`main`) and before the git commit (`finalize`), so every closed session commits consistent context files. Registration lives in memory for now because the processor shares its dependency surface with extraction (the `forkAndContinue` primitive and `MEMORY_FILE_TOOLS`) — the code marks this as transitional until the context extension grows its own processor wiring.
**Alternatives Considered**:
- `main` phase: would race with extraction processors that read AGENTS.md for deduplication
- Registering in `context/index.ts` today: forces a premature shared-wiring story for the fork primitive and the tool allowlist

**Consequences**:
- Pro: deterministic ordering relative to extraction and the workspace commit
- Con: the context feature is split across two extensions' wiring; disabling the memory extension also silently disables core context updates

## System Behavior

### Scenario: Ambiguous feedback recurs across sessions

**Given**: A staged signal "User seemed to prefer shorter responses" from a previous session
**When**: A new session closes where the user says "your answers are way too long"
**Then**: The forked agent sees the signal as `S1` in its follow-up instruction, updates SOUL.md with the conciseness preference, and deletes the promoted line from the signals file.

### Scenario: Session closes with no transcript

**Given**: A `PostProcessorContext` whose `transcriptPath` is null (the coordinator fills it from the session record's `piSessionFile`, so this is a session that produced no transcript)
**When**: Post-processing reaches `core-context`
**Then**: The processor's `process({ transcriptPath, log })` sees `transcriptPath == null`, logs a debug line, and returns without forking.

### Scenario: All staged signals have expired

**Given**: A signals file whose entries are all older than 30 days
**When**: The processor runs
**Then**: `cleanPendingSignals` deletes the file, and the prompt's signals section reads "No pending signals at this time."

## Notes

- Date comparison for expiry is lexicographic over zero-padded `YYYY-MM-DD` strings, using the local-timezone date (`localIsoDate`).
- The prompt instructs the agent to verify workspace claims (paths, configuration values) by reading/grepping before writing them, and to omit unverifiable claims — validation by instruction, not by host code.
- `SOUL_TEMPLATE` and `USER_TEMPLATE` seed a brand-new workspace; AGENTS.md has no template because pi treats a missing AGENTS.md as simply absent.

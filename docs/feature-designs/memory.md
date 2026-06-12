# Design: Memory

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/memory.md](../feature-specs/memory.md)
**Status**: Current

## Purpose

Explains how long-term memory is rebuilt on pi primitives: transcript re-reading plus headless side runs instead of the Python stack's SDK session forking, MCP tool servers, and scoped permissions.

## Problem Context

The Python implementation extracted memories by forking the live Claude SDK session and constraining the fork with permission rules and MCP tools. pi offers none of that: sessions are in-process JSONL trees, there is no MCP, and tool scoping is by tool list only (see `docs/reference/pi-sdk-notes.md`). Memory extraction therefore has to work from the persisted transcript and express all policy — store routing, deduplication, consolidation, validation — in prompts executed by ephemeral headless agents.

**Constraints:**
- Headless runs (`SideRunner.run`) get pi built-in file tools only; there is no delete tool and no per-directory write enforcement
- Extraction prompts must be bounded — transcripts can exceed any context window
- Everything is registered through the extension API (DES-001) and validated without live LLM calls (DES-002)

**Interactions:**
- Post-processing phases (`main` → `preFinalize` → `finalize`) are run by the coordinator on session close (see [conversation-loop](./conversation-loop.md))
- The `core-context` processor is registered here, between extraction and the git commit (see [foundational-context](./foundational-context.md))
- The git extension's `git-commit` processor (also `finalize`) commits workspace changes after each session
- Headless runs and model tiers come from the agent manager (see [agent-integration](./agent-integration.md)); crons from the core scheduler (see [core-shell](./core-shell.md))

## Design Overview

`src/extensions/memory/index.ts` only wires: a bootstrap hook for the layout, a context provider for index injection, one extraction processor per store, the core-context and transcript-archive processors, and three maintenance crons. All logic lives in sibling modules taking narrow dependencies (`Pick<SideRunner, "run">`), so tests fake the runner and assert on the options it receives.

```
message ──> memory-index provider ──> <context owner="memories"> layout + indexes
session close ──> main:        memory-episodic | memory-facts | memory-preferences (parallel)
                  preFinalize: core-context
                  finalize:    transcript-archive | git-commit
nightly cron ──> runMaintenanceTick(store) ──> headless run ──> sweepEmptyMarkdown
```

Each extraction renders the transcript to role-prefixed text, composes the store's system prompt from shared sections, runs the headless agent, then sweeps emptied files.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/memory/index.ts` | Wiring and config schema (`enabled`, `maxTranscriptChars`, `maintenance`) | Registers `createCoreContextProcessor` on behalf of the context extension (marked transitional); staggered maintenance schedules as config defaults |
| `src/extensions/memory/layout.ts` | Store paths, `ensureMemoryLayout` (dirs + index seeding), `sweepEmptyMarkdown`, `fileExists` | Placeholder index entries for pre-existing files; sweep treats ≤64-byte whitespace-only files as empty |
| `src/extensions/memory/indexes.ts` | `createMemoryIndexProvider`, `formatMemoryIndex` | Strict entry regex — malformed lines vanish; layout section always injected when `memories/` exists |
| `src/extensions/memory/extraction.ts` | `createExtractionProcessor` per store; store base prompts; prompt assembly | `main` phase; skip on missing/empty transcript; sweep after every run |
| `src/extensions/memory/prompts.ts` | Shared prompt sections: store purpose, classification examples, context dedup, workspace validation, index update, light index maintenance, scope | The scope section defines the empty-file deletion protocol |
| `src/extensions/memory/transcript.ts` | `parseTranscript`, `renderConversation`, `loadConversation` | Text-only turns; tail-priority truncation with marker |
| `src/extensions/memory/archive.ts` | `createTranscriptArchiveProcessor` (`finalize`) | Names archive after the JSONL header session id; never throws |
| `src/extensions/memory/maintenance.ts` | `runMaintenanceTick`, per-store maintenance prompts, `buildCrossStoreManifest`, `maintenanceSystemPrompt` | Injectable `now` clock for the Sunday rebuild dispatch |
| `src/extensions/memory/dates.ts` | `localIsoDate` | Local timezone — memory filenames follow the user's day, not UTC |

## Key Decisions

### Transcript re-read plus headless run replaces session forking

**Choice**: Extraction processors read the persisted pi JSONL (`loadConversation`), render user/assistant text capped at `maxTranscriptChars` with tail priority, and run an ephemeral in-memory pi session (`app.agent.side.run`) with file tools.
**Why**: This is pi's documented post-processing pattern (open the transcript read-only, run one-shot extraction). Forking the live session is unnecessary — the extraction agent needs the conversation content, not the conversational context — and an ephemeral session leaves nothing on disk and binds no Tachikoma extensions.
**Alternatives Considered**:
- Seeding an agent session with `session.agent.state.messages` (true fork): carries tool noise and unbounded context into every extraction
- `complete()` one-shots without tools: the agent could not read existing memories, grep for overlap, or edit files — the entire dedup/consolidation policy depends on tools

**Consequences**:
- Pro: extraction cost is bounded by the char cap; the tail bias keeps a session's conclusions
- Pro: processors are pure functions of `(transcriptPath, workspaceRoot)` — trivially testable with a faked runner
- Con: tool activity and thinking are invisible to extraction; only what was said survives into memory

### Empty-file deletion protocol with host-side sweep

**Choice**: Agents have no delete capability; the scope prompt instructs them to overwrite obsolete files with empty content, and the host runs `sweepEmptyMarkdown` over the target store after every extraction and maintenance run.
**Why**: pi's built-in tool set has no delete, and granting `bash` for `rm` would hand an unsandboxed shell to a prompt-driven agent. Emptying is expressible with the `write` tool and is idempotent.
**Alternatives Considered**:
- Custom delete tool on the side session: viable, but widens the blast radius of a misbehaving extraction beyond the swept directory
- Allowing bash restricted by prompt: prompt-level restriction is not enforcement

**Consequences**:
- Pro: worst-case agent damage within deletion semantics is an emptied file, recoverable from git
- Pro: the sweep doubles as cleanup for any stray empty files
- Con: deletion intent is implicit — a legitimately empty file would be removed too (none exist by design; every store file carries content)

### Static index injection instead of retrieval

**Choice**: A context provider injects the memory layout description plus the parsed facts/preferences `MEMORY.md` indexes on every message; the agent greps/reads memory files on demand. No similarity search, no per-message retrieval call.
**Why**: Indexes are small and always relevant; pushing file discovery to the agent's own tools avoids a retrieval subsystem and mirrors the proven Python static-index approach. Episodic files are date-organized and addressed by the layout description alone.
**Alternatives Considered**:
- Per-message LLM retrieval/selection: adds latency and a failure mode to every message for marginal gain at current store sizes
- Inlining full memory contents: blows up the context window as the store grows

**Consequences**:
- Pro: constant, predictable context overhead per message; zero extra LLM calls
- Pro: index quality is maintained by the same agents that write the files (index update rules in every prompt)
- Con: recall depends on the agent choosing to read files — a weak index description hides a memory

### Policy lives in composed prompt sections

**Choice**: Store policy (routing, dedup, validation, consolidation, index upkeep, scope) is encoded as module-level prompt constants in `prompts.ts`, composed per store in `STORE_PROMPTS` and per maintenance tick in `maintenanceSystemPrompt`.
**Why**: The same authority hierarchy, classification examples, and index rules apply across extraction and maintenance; shared constants keep the stores' behavior consistent and reviewable as text, with `$WORKSPACE`/`{date}` substitution as the only templating.
**Alternatives Considered**:
- Per-store monolithic prompts: drift between stores was the observed failure mode in the Python project

**Consequences**:
- Pro: changing a policy (e.g. the authority order) is a one-line diff applying everywhere
- Pro: tests assert prompt composition cheaply (substring checks on the faked runner's options)
- Con: behavior is enforced by instruction, not code — regressions surface as store quality drift, not test failures

### Staggered cron schedules instead of a shared schedule with a semaphore

**Choice**: Each store's maintenance is an independently scheduled cron (`0 3`, `20 3`, `40 3` by default), and the weekday/Sunday index strategy is decided by an injectable clock at tick time.
**Why**: The core scheduler has named, overlap-protected jobs but no priority semaphore (unlike the Python scheduler); staggering by 20 minutes achieves the same "don't pile up three headless agent runs" goal with zero new machinery.
**Alternatives Considered**:
- One cron fanning out sequentially: a single failure or hang stalls the remaining stores; per-job overlap protection is lost

**Consequences**:
- Pro: each tick is independently triggerable, loggable, and disableable by reconfiguring its schedule
- Con: the stagger is convention — a long episodic run can still overlap the facts run

## System Behavior

### Scenario: Session close pipeline

**Given**: A session closes with a persisted transcript
**When**: Post-processing runs
**Then**: The three extraction processors run in parallel in `main` (failures isolated per processor, recorded in `postProcessingState`), `core-context` runs in `preFinalize`, and `transcript-archive` and `git-commit` run in `finalize` — so memory and context edits are committed with the session.

### Scenario: Facts agent merges two files

**Given**: `memories/facts/` contains two overlapping files
**When**: The facts extraction or maintenance agent consolidates them, writing the merged content into one and emptying the other
**Then**: After the run, `sweepEmptyMarkdown` deletes the emptied file and the prompt's index rules have the agent update `MEMORY.md` to match.

### Scenario: Sunday index rebuild

**Given**: The preferences maintenance cron fires on a Sunday
**When**: `maintenanceSystemPrompt` is assembled
**Then**: The prompt carries the full `## Memory Index Rebuild (full)` section (describe every file, consider structural merges/renames, rewrite `MEMORY.md` from scratch) instead of the weekday consistency check.

## Notes

- `transcript-archive` and `git-commit` share the `finalize` phase and run concurrently, so an archived transcript may land in the working tree after that session's commit — the git processor's second commit pass (it re-checks for uncommitted changes) or the next session's commit picks it up.
- Completed processors are recorded per name on the session row and skipped on re-runs, which makes crash-recovered post-processing (dangling sessions closed at startup) resume where it left off.
- `localIsoDate` deliberately uses the local timezone so episodic filenames match the user's perception of "today".
- The cross-store manifest lists names and paths only — content stays out of maintenance prompts to keep them bounded.

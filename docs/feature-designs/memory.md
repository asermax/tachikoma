# Design: Memory

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/memory.md](../feature-specs/memory.md)
**Status**: Current

## Purpose

Explains how long-term memory is built on pi primitives: forking the just-ended session and continuing it as a tool-restricted follow-up turn.

## Problem Context

pi sessions are in-process JSONL trees stored on disk and forkable: `SessionManager.forkFrom` copies a session file's history into a fresh session without mutating the source, and `tools` on `createAgentSession` is an allowlist independent of the system prompt (see `docs/reference/pi-sdk-notes.md`). Memory extraction leans on this: at session close it forks the just-ended conversation so the same assistant — full history live, persona intact — folds it into the stores, with policy (store routing, deduplication, consolidation, validation) expressed in a follow-up user instruction. Tool discipline is enforced by a hard `MEMORY_FILE_TOOLS` allowlist on the fork, not by a separate ephemeral persona.

**Constraints:**
- The fork gets a hard file-tool allowlist (`MEMORY_FILE_TOOLS`); there is no delete tool and no per-directory write enforcement, so emptying-a-file plus a host sweep stands in for deletion
- The fork loads the whole conversation as live history — fidelity is full, but cost scales with conversation length (×3 stores, run in parallel)
- Everything is registered through the extension API (DES-001) and validated without live LLM calls (DES-002) — tests fake `forkAndContinue` and assert on its arguments

**Interactions:**
- Post-processing phases (`main` → `preFinalize` → `finalize`) are run by the coordinator on session close (see [conversation-loop](./conversation-loop.md))
- The `core-context` processor is registered here, between extraction and the git commit (see [foundational-context](./foundational-context.md))
- The git extension's `git-commit` processor (also `finalize`) commits workspace changes after each session
- Headless runs and model tiers come from the agent manager (see [agent-integration](./agent-integration.md)); crons from the core scheduler (see [core-shell](./core-shell.md))

## Design Overview

`src/extensions/memory/index.ts` only wires: a bootstrap hook for the layout, a context provider for index injection, one extraction processor per store, the core-context and transcript-archive processors, three store maintenance crons, a foundational-context maintenance cron, and a transcript-prune cron. Extraction and core-context take a narrow `Pick<AgentManager, "forkAndContinue">` dependency; the maintenance ticks take `Pick<SideRunner, "run">` (they have no conversation to fork). Tests fake those and assert on the arguments they receive.

```
message ──> memory-index provider ──> <context owner="memories"> layout + indexes
session close ──> main:        memory-episodic | memory-facts | memory-preferences (parallel forks)
                  preFinalize: core-context (fork)
                  finalize:    transcript-archive | git-commit
nightly cron ──> runMaintenanceTick(store) ──> headless run ──> sweepEmptyMarkdown
nightly cron ──> runContextMaintenanceTick ──> headless run (SOUL/USER/AGENTS, cleanup-only, no sweep)
nightly cron ──> pruneTranscripts ──> delete transcripts older than retentionDays (no agent)
```

Each extraction forks the just-ended pi session (`forkAndContinue`), composes the store's instruction from shared sections, hands the forked assistant one follow-up turn under a file-tool allowlist, then sweeps emptied files. The nightly maintenance ticks differ: they have no conversation to fork, so they run bare headless side-runs (`SideRunner.run`) over the store on disk.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/memory/index.ts` | Wiring and config schema (`enabled`, `maintenance`) | Registers `createCoreContextProcessor` on behalf of the context extension (marked transitional); staggered maintenance schedules as config defaults |
| `src/extensions/memory/layout.ts` | Store paths, `ensureMemoryLayout` (dirs + index seeding), `sweepEmptyMarkdown`, `fileExists` | Placeholder index entries for pre-existing files; sweep treats ≤64-byte whitespace-only files as empty |
| `src/extensions/memory/indexes.ts` | `createMemoryIndexProvider`, `formatMemoryIndex` | Strict entry regex — malformed lines vanish; layout section always injected when `memories/` exists |
| `src/extensions/memory/extraction.ts` | `createExtractionProcessor` per store; store base prompts (follow-up user-instruction shape); `storeInstruction`/`MEMORY_FILE_TOOLS`; the silent-background section | `main` phase; forks via `forkAndContinue` with the `MEMORY_FILE_TOOLS` allowlist; skip on missing transcript; sweep after every run |
| `src/extensions/memory/prompts.ts` | Shared prompt sections: store purpose, classification examples, context dedup, workspace validation, index update, light index maintenance, scope | The scope section defines the empty-file deletion protocol |
| `src/extensions/memory/archive.ts` | `createTranscriptArchiveProcessor` (`finalize`) writes archives; `pruneTranscripts` (nightly cron) deletes old ones | Names archive after the JSONL header session id; age-based prune by file mtime; both never throw |
| `src/extensions/memory/maintenance.ts` | `runMaintenanceTick`, per-store maintenance prompts, `buildCrossStoreManifest`, `maintenanceSystemPrompt`; `runContextMaintenanceTick`, `contextMaintenanceSystemPrompt`, `buildStoreManifestForContext` for foundational-context cleanup | Injectable `now` clock for the Sunday rebuild dispatch; context tick is cleanup-only and runs no sweep (edits in place); both `runMaintenanceTick` and `runContextMaintenanceTick` call `commitChanges` after the headless run so each nightly pass commits its edits (`chore(memory): scheduled <store>/context file maintenance`) — the transcript prune does not commit |
| `src/extensions/memory/dates.ts` | `localIsoDate` | Local timezone — memory filenames follow the user's day, not UTC |

## Key Decisions

### Fork-continue the just-ended session instead of replaying the transcript as text

**Choice**: Extraction processors fork the just-ended pi session (`forkAndContinue`, backed by `SessionManager.forkFrom`) and hand the same assistant — full conversation live in its history, composed persona intact — one follow-up user instruction, hard-limited to `MEMORY_FILE_TOOLS`. The source transcript is never mutated. This mirrors the legacy Python implementation, which forked the SDK session (`fork_session=True`) rather than replaying text.
**Why**: The agent that just had the conversation already holds it in full fidelity (real turns, tool activity, thinking), so "use what you already know" is more faithful and natural than re-reading a flattened, truncated `<conversation>` blob. `forkFrom` copies the history into a fresh file without touching the original; the `tools` allowlist (independent of persona) keeps the fork from messaging the user or firing tasks even though it reuses the live session.
**Alternatives Considered**:
- Transcript re-read + ephemeral headless run (the prior TS shape): bounded cost but lossy — flattened to user/assistant text, tail-truncated at a char cap, tool activity and thinking discarded
- `complete()` one-shots without tools: the agent could not read existing memories, grep for overlap, or edit files — the entire dedup/consolidation policy depends on tools

**Consequences**:
- Pro: full-fidelity context — the extraction agent sees exactly what it lived, not a truncated rendering
- Pro: processors are thin over `forkAndContinue(transcriptPath, instruction, "processor", MEMORY_FILE_TOOLS)` — tested by faking the forker and asserting its arguments
- Con: the fork loads the whole conversation as live tokens, so cost scales with conversation length (×3 stores, parallel); no char cap bounds it

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
**Why**: Indexes are small and always relevant; pushing file discovery to the agent's own tools avoids a retrieval subsystem entirely. Episodic files are date-organized and addressed by the layout description alone.
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
- Per-store monolithic prompts: invite policy drift between stores

**Consequences**:
- Pro: changing a policy (e.g. the authority order) is a one-line diff applying everywhere
- Pro: tests assert prompt composition cheaply (substring checks on the faked runner's options)
- Con: behavior is enforced by instruction, not code — regressions surface as store quality drift, not test failures

### Staggered cron schedules instead of a shared schedule with a semaphore

**Choice**: Each store's maintenance is an independently scheduled cron (`0 3`, `20 3`, `40 3` by default), the foundational-context cleanup is its own cron (`0 4` by default), and the weekday/Sunday index strategy is decided by an injectable clock at tick time.
**Why**: The core scheduler has named, overlap-protected jobs but no priority semaphore; staggering the headless agent runs by 20 minutes (and the context tick to the top of the next hour, well clear of the 03:50 transcript prune) achieves the "don't pile up headless agent runs" goal with zero new machinery.
**Alternatives Considered**:
- One cron fanning out sequentially: a single failure or hang stalls the remaining stores; per-job overlap protection is lost

**Consequences**:
- Pro: each tick is independently triggerable, loggable, and disableable by reconfiguring its schedule
- Con: the stagger is convention — a long episodic run can still overlap the facts run

### Cleanup-only periodic pass over the foundational context files

**Choice**: `runContextMaintenanceTick` mirrors the store maintenance shape (headless `processor`-tier run over a conservative prompt) but targets the workspace-root `SOUL.md`/`USER.md`/`AGENTS.md` and is strictly cleanup-only: it reviews for staleness, redundancy, overlap, and size (the same ~120/~400-line limits the per-session `core-context` processor enforces) and applies edits in place. It adds no new content — extracting new signals from conversations remains the `core-context` processor's job. The prompt carries the shared store-purpose section plus a names-only memory-store manifest (`buildStoreManifestForContext`) so the agent trims context sections that duplicate the more authoritative facts store down to pointers. It runs no `sweepEmptyMarkdown` because these are stable, always-present files edited in place — never emptied.
**Why**: The context files drift between the per-session updates (resolved projects linger, sections duplicate, AGENTS.md bloats), and the per-session processor only sees one conversation at a time. A periodic whole-file pass is the natural place to consolidate and prune, exactly as the store ticks do for memory. Reusing the side-run mechanism, the conservative-edit conventions, and the names-only manifest pattern keeps it consistent with the rest of maintenance and warrants no new ADR/DES (reuses ADR-006 scheduler, ADR-007 config).
**Alternatives Considered**:
- Folding context cleanup into the per-session `core-context` processor: would run on every session close (cost) and still only ever see one conversation, missing cross-section consolidation
- A `MemoryStore`-style fourth store: the context files live at the workspace root with no index, no per-store directory, and a stricter (especially-conservative-for-SOUL, never-empty) edit policy — modeling them as a store would distort all three abstractions

**Consequences**:
- Pro: context files get the same periodic consolidation the memory stores get, keeping them a current snapshot rather than an append-only log
- Con: cleanup quality is enforced by prompt instruction, not code — over-aggressive pruning would surface as context drift, mitigated by the conservative/cleanup-only framing and the especially-conservative SOUL.md guard

### Deterministic age-based transcript pruning instead of an agent tick

**Choice**: Transcript retention is a plain `fs` routine (`pruneTranscripts` in `archive.ts`) on its own nightly cron — it lists `memories/transcripts/`, deletes `.jsonl` files whose mtime is strictly older than `transcriptRetentionDays`, and never runs a headless agent. Age is read from the file's mtime because archive filenames are pi session ids, not dates; `0` retention disables it (retain forever), and the cron is gated by the same `maintenance.enabled` switch as the store ticks.
**Why**: Transcripts are raw JSONL archives, not prose that benefits from consolidation. They need only age-based deletion, so an LLM run would add cost and a failure surface for no benefit. Pruning lives in `archive.ts` (which already owns transcript writing) and reuses the tolerant `sweepEmptyMarkdown` shape and the `maintenance.ts` injectable-clock pattern; it warrants no new ADR/DES (reuses ADR-006 scheduler, ADR-007 config).
**Alternatives Considered**:
- A fourth `runMaintenanceTick` store: forces an agent run and a `processor`-tier LLM call onto a job that is pure file deletion
- Folding the prune into the episodic tick: couples deterministic deletion to an LLM run's schedule and success

**Consequences**:
- Pro: cheap, predictable, no LLM call; tolerant of I/O errors (never throws)
- Con: deletion is unconditional by age — a transcript whose extracted memories failed to capture something is gone after the window (mitigated by git history until the workspace is also pruned)

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

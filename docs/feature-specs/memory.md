# Memory

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The `memory` extension (`src/extensions/memory/`) maintains long-term memory as git-versioned markdown under the workspace `memories/` directory, organized into three stores — episodic (date-stamped summaries), facts (topic-named reference information), preferences (topic-named subjective choices) — plus raw transcript archives. A static index of the store is injected into every message's context; extraction processors fold each closed conversation into the stores via headless agent runs; nightly maintenance crons consolidate and prune each store, and a separate nightly job prunes transcript archives older than a configurable retention window.

Memory agents have no delete tool: files to be removed are emptied by the agent and swept by the host afterwards. Facts and preferences each carry a `MEMORY.md` index mapping filenames to one-line descriptions, kept in sync at write time and rebuilt weekly. The extension's setup also hosts the registration of the `core-context` processor (see [foundational-context](./foundational-context.md)).

## User Stories

- As the system, I need learnings from completed conversations persisted automatically so that future sessions are aware of past interactions, facts, and preferences
- As a user, I want memories stored as plain markdown in my workspace so that I can inspect, edit, and version them
- As the system, I need periodic consolidation so that the store doesn't degrade from accumulated staleness, fragmentation, and verbosity

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The `init-memory-layout` bootstrap hook creates `memories/episodic/`, `memories/facts/`, `memories/preferences/`, and `memories/transcripts/` idempotently |
| R1 | Bootstrap seeds `MEMORY.md` in facts and preferences: header-only when the directory is empty, placeholder entries (`Description pending update`) for pre-existing files, and leaves an existing index untouched |
| R2 | A `memory-index` context provider injects a `memories`-tagged block on every message: a layout section describing the stores plus the parsed facts and preferences indexes; it returns null when `memories/` does not exist |
| R3 | Index entries must match `[Name](./file.md): description`; malformed lines are skipped, and an index with no usable entries contributes no section |
| R4 | One post-processor per store (`memory-episodic`, `memory-facts`, `memory-preferences`), all in the `main` phase, runs at session close |
| R5 | Transcript parsing keeps only user/assistant text from the pi JSONL — tool calls, tool results, thinking blocks, and non-message entries are dropped; malformed lines are skipped |
| R6 | The rendered conversation is capped at `maxTranscriptChars` (default 24000) with tail priority — newest turns are kept and a truncation marker is prepended when older turns are dropped |
| R7 | Extraction is skipped (no LLM call) when the session has no transcript or the transcript renders to an empty conversation |
| R8 | Each extraction runs a headless agent (`app.agent.side.run`) on the `processor` tier with file tools (`read`, `grep`, `find`, `ls`, `edit`, `write`) and a store-specific system prompt with `$WORKSPACE` and `{date}` substituted |
| R9 | Episodic extraction writes exactly one local-date file per day (`YYYY-MM-DD.md`), merging into an existing day file and folding variant-named files back into the canonical one; trivial conversations may produce nothing |
| R10 | Facts extraction targets stable reference information with broad topic filenames; it searches for overlap before creating files, consolidates at write time, prunes contradicted entries, and keeps files under ~40 lines |
| R11 | Preferences extraction targets subjective choices; it applies a facts-vs-preferences self-check, skips preferences already captured in AGENTS.md, and keeps files under ~30 lines |
| R12 | Facts and preferences prompts include the shared sections from `src/extensions/memory/prompts.ts`: classification examples, store purpose/authority hierarchy, context-file deduplication, workspace claim validation, and index update rules |
| R13 | Extraction and maintenance agents are scoped by prompt to write only within their target store directory; deletion is expressed by emptying a file |
| R14 | After each extraction and maintenance run, `sweepEmptyMarkdown` removes empty or whitespace-only `.md` files from the target store, ignoring missing directories and non-markdown files |
| R15 | A `transcript-archive` post-processor (`finalize` phase) copies the pi session JSONL to `memories/transcripts/<pi-session-id>.jsonl` (id from the JSONL header, falling back to the source filename); it never throws — failures are logged warnings |
| R16 | Five maintenance cron jobs run on staggered schedules — one per store (defaults 03:00 / 03:20 / 03:40 daily), a foundational-context cleanup (`memory-context-maintenance`, default 04:00), and a transcript prune (`memory-transcripts-maintenance`, default 03:50) — all registered only when `[extensions.memory] maintenance.enabled` is true |
| R17 | Episodic maintenance applies configurable time tiers: clean recent dailies (default 15 days), consolidate into weekly `YYYY-WNN.md` (to 3 months), then monthly `YYYY-MM.md` (to 12 months), delete beyond that |
| R18 | Facts maintenance evaluates staleness, redundancy, overlap, cluster consolidation (3+ files sharing a prefix/topic merge into one broad file), size limits, and context-file overlap; preferences maintenance additionally detects misclassified factual content for removal |
| R19 | Facts/preferences maintenance prompts include light index-consistency instructions on weekdays and a full `MEMORY.md` rebuild on Sundays; episodic maintenance includes no index section |
| R20 | Maintenance prompts include the store-purpose section, a names-only cross-store manifest (other stores plus existing root context files; omitted when nothing else exists), contradiction-detection instructions, and the scope section |
| R20a | The foundational-context maintenance tick reviews `SOUL.md`, `USER.md`, `AGENTS.md` at the workspace root for staleness, redundancy, overlap, and size (USER.md ~120 lines, AGENTS.md ~400 lines); it is cleanup-only (adds no new content — that is the per-session `core-context` processor's job), especially conservative for SOUL.md, runs no `sweepEmptyMarkdown` (edits in place, never empties), and its prompt carries the store-purpose section plus a names-only memory-store manifest so context defers to the more authoritative facts store (omitted when the stores are empty) |
| R21 | Configuration lives under `[extensions.memory]`: `enabled`, `maxTranscriptChars`, and `maintenance` (`enabled`, three store schedules, `contextSchedule`, `recentDays`, `weeklyThresholdMonths`, `monthlyThresholdMonths`, plus `transcriptsSchedule` and `transcriptRetentionDays` for transcript retention) |
| R22 | `enabled = false` registers nothing — no bootstrap hook, provider, processors, or crons |
| R23 | A transcript-prune cron deletes `.jsonl` files in `memories/transcripts/` whose modification time (set at archive write) is strictly older than `transcriptRetentionDays` (default 90); it is deterministic host-side deletion (no headless agent run), skips non-`.jsonl` files, never throws (missing dir is a silent no-op; per-file failures warn and continue), and is disabled — retaining transcripts forever — when `transcriptRetentionDays` is 0 |

## Behaviors

### Workspace Layout and Index Seeding (R0, R1)

**Acceptance Criteria**:
- Given an empty workspace, when bootstrap runs, then `memories/` contains `episodic`, `facts`, `preferences`, and `transcripts`, and the facts/preferences `MEMORY.md` files contain only the `# Memory Index` header
- Given pre-existing memory files without an index, when bootstrap runs, then each gets a placeholder entry with a Title Case name derived from its filename
- Given an existing `MEMORY.md`, when bootstrap runs again, then its content is preserved (idempotent)

### Static Index Injection (R2, R3)

**Acceptance Criteria**:
- Given facts and preferences indexes with entries, when a message is handled, then the injected block (tag `memories`) contains the layout section, a `## Facts Index` and/or `## Preferences Index` section with one bullet per entry, and on-demand reading instructions
- Given a header-only index, when the provider runs, then that store contributes no index section while the layout section is still injected
- Given no `memories/` directory, when the provider runs, then it returns null and contributes nothing
- Given malformed index lines, when the index is formatted, then well-formed entries are kept and malformed ones are dropped silently

### Extraction at Session Close (R4–R8, R13, R14)

**Acceptance Criteria**:
- Given a closed session with a transcript, when the `main` phase runs, then each store's processor renders the conversation and issues one headless run whose system prompt names the absolute store directory and whose prompt embeds the conversation in role-prefixed form
- Given a transcript with tool calls, tool results, thinking blocks, and malformed lines, when parsed, then only user and assistant text turns survive
- Given a conversation longer than `maxTranscriptChars`, when rendered, then the newest turns are kept, `[earlier conversation truncated]` is prepended, and a single overlong turn is tail-clipped to fit
- Given no transcript or an empty rendering, when a processor runs, then no headless run is issued
- Given the agent emptied a file (merge or obsolescence), when the run completes, then the sweep deletes it while non-empty files survive

### Transcript Archival (R15)

**Acceptance Criteria**:
- Given a transcript whose header line is `{"type":"session","id":"<id>",…}`, when the archive processor runs, then the file is copied verbatim to `memories/transcripts/<id>.jsonl`, recreating the directory if needed
- Given a transcript without a session header id, when archived, then the destination uses the source filename
- Given a missing source file or copy failure, when the processor runs, then it logs a warning and completes without throwing; a null transcript path skips with a debug log

### Scheduled Maintenance (R16–R20a, R23)

**Acceptance Criteria**:
- Given maintenance is enabled, when the extension loads, then `memory-episodic-maintenance`, `memory-facts-maintenance`, `memory-preferences-maintenance`, `memory-context-maintenance`, and `memory-transcripts-maintenance` cron jobs are registered on their configured schedules; given maintenance is disabled, then none are registered
- Given the transcript-prune tick runs with `transcriptRetentionDays = 90`, then `.jsonl` files in `memories/transcripts/` whose mtime is strictly older than 90 days are deleted, files at exactly the threshold and newer survive, non-`.jsonl` files are left untouched, and eligibility follows mtime rather than the filename
- Given the transcript-prune tick runs with `transcriptRetentionDays = 0`, then no transcript is deleted; given the transcripts directory is missing, then the tick completes silently; given one entry cannot be removed, then a warning is logged and the remaining files are still pruned
- Given the episodic tick runs, then its prompt embeds the configured tier thresholds and contains no index instructions
- Given a facts or preferences tick on Monday through Saturday, then the prompt includes the light index-consistency section; on Sunday it includes the full rebuild section instead
- Given a store tick completes, then emptied files in the target store are swept
- Given other stores or root context files exist, then the prompt includes a names-only cross-store manifest; given nothing else exists, the manifest is omitted
- Given the context tick runs, then its prompt reviews `SOUL.md`, `USER.md`, and `AGENTS.md`, is cleanup-only and especially conservative for SOUL.md, and carries no index instructions; given the memory stores hold files, the prompt embeds a names-only store manifest so context defers to facts; given the stores are empty, the manifest is omitted; the tick never empties or sweeps the context files

### Configuration (R21, R22)

**Acceptance Criteria**:
- Given no `[extensions.memory]` section, when the extension loads, then defaults apply: enabled, 24000 transcript chars, maintenance enabled with schedules `0 3 * * *` / `20 3 * * *` / `40 3 * * *` (stores), `0 4 * * *` (context), `50 3 * * *` (transcripts), thresholds 15 days / 3 months / 12 months, and `transcriptRetentionDays` 90
- Given `enabled = false`, when the extension loads, then setup logs the disabled state and registers nothing

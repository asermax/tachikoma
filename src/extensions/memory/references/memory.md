# Memory

The long-term memory store under `memories/`. Owned by the memory extension.

## Stores

| Store | Contents |
|-------|----------|
| `memories/episodic/` | Date-stamped conversation summaries (`YYYY-MM-DD.md`), consolidated into weekly `YYYY-WNN.md` and monthly `YYYY-MM.md` rollups over time |
| `memories/topics/` | Everything worth remembering about a subject — reference facts, preferences, insights, decisions, and conclusions together — one topic per file, indexed in that store's `MEMORY.md` |
| `memories/learnings/` | Recurring friction, hard constraints, hard-won lessons (experience, not knowledge), one theme per file, indexed in `MEMORY.md`; drafts are tentative, confirmed entries recurring |
| `memories/transcripts/` | Archived raw conversation transcripts (retention-pruned, default 90 days) |
| `memories/skill-evolution/` | Pattern pages and the impact ledger maintained by skill evolution — see its own guidance |

## How it is maintained

You do not write to `memories/`. At trunk close an automated pass runs per branch: it
extracts new episodic summaries and topic/learning entries (forking the branch's own
conversation so the extraction sees full context), consolidates and prunes the stores, and
commits the result. The indexes injected into your context each session (`Topics Index`,
`Learnings Index`) are snapshots of those stores' `MEMORY.md` files — read the linked file
when an entry looks relevant.

## Configuration

`[extensions.memory]`: `enabled` (default `true`) disables the whole store; under
`[extensions.memory.maintenance]`, `recentDays` (15), `weeklyThresholdMonths` (3) and
`monthlyThresholdMonths` (12) drive consolidation, `parallelizeExtraction` (true) extracts
stores concurrently, and `transcriptsSchedule` (`50 3 * * *`) / `transcriptRetentionDays`
(90; 0 keeps forever) drive transcript pruning.

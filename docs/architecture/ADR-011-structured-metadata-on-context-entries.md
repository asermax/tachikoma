# ADR-011: Structured Metadata on Context Entries

**Status**: Accepted
**Date**: 2026-04-07
**Last Updated**: 2026-04-07

## Context

Context entries (`SessionContextEntry`) store injected context as text content with an owner identifier. When the skills context provider needed to track which skills are already loaded in a session, the only option was to parse the content format (regex on XML tags) to identify individual skills. This couples identification to rendering format — fragile and error-prone.

The skill provider also needed to persist one entry per detected skill (rather than one combined entry) for clean tracking, requiring a way to identify which skill each entry represents.

## Decision

Add an optional `metadata` field (JSON dict, nullable TEXT column) to `SessionContextEntry`. Each entry type can store structured data relevant to its purpose:

- Skill entries: `{"skill_name": "name"}` — identifies which skill the entry represents
- Future entry types: can use metadata for different structured data

The field flows through the full pipeline:
1. `ContextResult.metadata` — provider sets structured data
2. `save_context_entries()` — coordinator passes metadata to persistence
3. `SessionContextEntry.metadata` — persisted and queryable

The coordinator passes metadata through without interpretation — it's provider-owned data.

## Consequences

### Positive

- Structured, type-safe identification without content parsing
- Extensible — future entry types can use metadata for different purposes without schema changes
- No coupling between rendering format and identification logic
- Backward compatible — nullable column, existing entries have `metadata=None`

### Negative

- Requires DB migration (nullable TEXT column)
- `save_context_entries` interface changed from `list[tuple[str, str]]` to `list[tuple[str, str, dict | None]]` — all call sites updated

## Alternatives Considered

### Regex parsing from content

- **Description**: Parse skill names from XML content to identify loaded skills
- **Why rejected**: Fragile — ties identification to rendering format. Content format may change independently of tracking needs. Code smell identified during plan review.

### Separate tracking table

- **Description**: A dedicated table mapping session → loaded skill names
- **Why rejected**: Over-engineered for the use case. Metadata on the existing entry model is simpler and colocated with the data it describes.

### One combined entry per classification

- **Description**: Store all detected skills in a single entry (avoiding need for per-entry identification)
- **Why rejected**: Can't identify individual skills for filtering. Makes accumulation (appending new skills) impossible without replacing the entire entry.

---

## Notes

- Migration: `ALTER TABLE session_context_entries ADD COLUMN metadata TEXT` — nullable, no data migration needed for existing rows
- The motivating use case was DLT-075 (per-message skill re-evaluation), but the field is designed for general-purpose use

# ADR-003: Schema Language

**Status**: Accepted
**Date**: 2026-06-11

## Context

Runtime validation shows up in three places: tool parameter schemas (pi's `defineTool`), configuration validation (TOML config), and structured LLM output (extraction processors via `pi-ai`). pi itself builds on **typebox 1.x** — the unscoped `typebox` package, a major rewrite distinct from the widely-known `@sinclair/typebox` 0.x line. Using a different schema library (or a different TypeBox lineage) would mean converting schemas at every pi boundary.

## Decision

Use **TypeBox 1.x everywhere** (`typebox@1.1.38`, kept in lockstep with the version pi ships). One schema language for:

- Tool parameter schemas passed to `pi.registerTool` / `defineTool`
- Configuration schema validation with defaults
- Structured output schemas for side-channel `complete()` calls

Conventions: use `StringEnum` from `pi-ai` instead of `Type.Union` of literals (provider compatibility), and derive static types from schemas (`Static<typeof Schema>`) rather than maintaining parallel interfaces.

## Consequences

### Positive

- Zero impedance mismatch with pi — schemas flow into `defineTool` and `pi-ai` validation untouched
- Single schema vocabulary across tools, config, and LLM output; types are inferred, never duplicated
- JSON Schema-native, so schemas serialize directly into provider payloads

### Negative

- **Version trap**: `typebox` 1.x and `@sinclair/typebox` 0.34 coexist on npm with different APIs; most ecosystem docs and examples target the old package. Mitigated by pinning and a note in the SDK reference
- 1.x is young; API surface may shift alongside pi upgrades — the lockstep pin makes those bumps deliberate

## Alternatives Considered

- **Zod**: excellent DX but requires JSON Schema conversion at every pi boundary and a second schema language in the codebase
- **@sinclair/typebox 0.34**: the legacy lineage; diverges from what pi depends on and would eventually force a migration anyway

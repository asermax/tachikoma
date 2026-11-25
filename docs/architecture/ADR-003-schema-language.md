# ADR-003: Schema Language

**Status**: Accepted
**Date**: 2026-06-11

## Context

Runtime validation shows up in three places: tool parameter schemas (pi's `defineTool`), configuration validation (TOML config), and structured LLM output (extraction processors via `pi-ai`). pi itself builds on **typebox 1.x** — the unscoped `typebox` package, a major rewrite distinct from the widely-known `@sinclair/typebox` 0.x line. Using a different schema library (or a different TypeBox lineage) would mean converting schemas at every pi boundary.

pi's own use of schemas splits in two. `pi-coding-agent` builds its tool and settings schemas with TypeBox and pins the package's exact version. Its agent and AI packages (`@earendil-works/pi-agent-core`, `@earendil-works/pi-ai`) depend on `@earendil-works/pi-schema` instead: pi validates tool arguments as JSON Schema, and a TypeBox schema works there because a TypeBox schema is JSON Schema.

## Decision

Use **TypeBox 1.x everywhere**, pinned to the exact version `@earendil-works/pi-coding-agent` depends on and kept in lockstep with it (`typebox@1.3.27` for pi-coding-agent 1.6.0), so the tree holds one copy of the library. One schema language for:

- Tool parameter schemas passed to `pi.registerTool` / `defineTool`
- Configuration schema validation with defaults
- Structured output schemas for side-channel `complete()` calls

Conventions: use Tachikoma's `StringEnum` (`src/util/string-enum.ts`) instead of `Type.Union` of literals (provider compatibility) — pi-ai's own `StringEnum` returns a pi-schema `Schema`, which neither type-checks inside a TypeBox object nor yields a `Static` union — and derive static types from schemas (`Static<typeof Schema>`) rather than maintaining parallel interfaces. A schema that crosses into pi must stay plain JSON Schema.

## Consequences

### Positive

- Zero impedance mismatch with pi — a TypeBox schema is JSON Schema, so tool parameters flow into `defineTool` unchanged and pi validates them as such
- Single schema vocabulary across tools, config, and LLM output; types are inferred, never duplicated
- JSON Schema-native, so schemas serialize directly into provider payloads

### Negative

- **Version trap**: `typebox` 1.x and `@sinclair/typebox` 0.34 coexist on npm with different APIs; most ecosystem docs and examples target the old package. Mitigated by pinning and a note in the SDK reference
- 1.x is young; API surface may shift alongside pi upgrades — the lockstep pin makes those bumps deliberate
- **No compile-time signal outside JSON Schema**: `defineTool` accepts a TypeBox feature that JSON Schema has no equivalent for, and pi then ignores it. A refinement is not run, a codec does not decode, and a `BigInt` or `Void` parameter is converted or rejected rather than checked as written. `tests/schema-guard.test.ts` guards against this: every registered schema must survive a JSON round-trip and use only JSON Schema `type` values. The guard reads the JSON, so a refinement (`Type.Refine`) or a codec (`Type.Codec`) is invisible to it, and pi ignores both; such behavior needs a test of the tool's own

## Alternatives Considered

- **Zod**: excellent DX but requires JSON Schema conversion at every pi boundary and a second schema language in the codebase
- **@sinclair/typebox 0.34**: the legacy lineage; diverges from what pi depends on and would eventually force a migration anyway

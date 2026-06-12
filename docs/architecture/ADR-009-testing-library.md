# ADR-009: Testing Library

**Status**: Accepted
**Date**: 2026-06-11

## Context

The project needs a test runner that handles TypeScript ESM sources natively (no separate compile step, consistent with ADR-002), supports async-heavy code, and provides first-class mocking and fake timers — the scheduler, idle gating, and buffered delivery are all timing-driven and untestable without time control.

## Decision

Use **vitest** as the test framework.

- Native TS/ESM execution — tests import `.ts` sources directly, same as the runtime
- `vi.mock`/`vi.fn` for module and function mocking; `vi.useFakeTimers` for scheduler, idle-timeout, and buffer tests
- `vitest run` in CI/quality gates, watch mode for development
- Conventions: tests under `tests/`, real (in-memory or temp-file) SQLite over mocked repositories where practical

## Consequences

### Positive

- Zero-config fit with the type-stripped, ESM, `.ts`-extension setup
- Fast watch mode with smart re-runs; parallel by default
- Fake timers and tight async assertions cover Tachikoma's most failure-prone logic (timing windows, idle gates, catch-up)
- Jest-compatible API — familiar idioms, broad documentation

### Negative

- Vitest executes tests through its own pipeline (vite-node transform), not Node's type stripping — subtle environment differences are possible; `erasableSyntaxOnly` keeps sources valid under both
- Heavier dev dependency than node:test; accepted for the mocking/timer ergonomics

## Alternatives Considered

- **node:test**: built-in and lightweight, but mocking and fake-timer support are immature relative to what the timing-driven subsystems need
- **Jest**: ESM + TypeScript support remains configuration-heavy; vitest provides the same API without the friction

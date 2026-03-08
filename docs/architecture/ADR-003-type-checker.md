# ADR-003: Type Checker

**Status**: Accepted
**Date**: 2026-03-08

## Context

The Tachikoma project requires static type checking to maintain high code quality and catch errors early in development. Type checking serves several critical purposes:

- **Early error detection**: Catch type errors at development time rather than runtime
- **Code quality**: Maintain consistency and prevent common Python typing mistakes
- **IDE support**: Enable better autocomplete, refactoring, and navigation through LSP integration
- **AI coding guardrails**: Type hints provide constraints that keep AI-generated code on track

## Decision

Use **ty** (Astral's static type checker) as our type checking tool with the following requirements:

- **Pre-commit enforcement**: ty must run before every commit
- **Zero-tolerance policy**: All typing errors must be fixed before code can be committed
- **Strict typing mode**: Enable strict type checking to catch maximum errors
- **Complete type coverage**: All functions, methods, and public APIs must have explicit type annotations

## Consequences

### Positive

- **Faster development feedback**: Catch type errors immediately during development
- **Better refactoring confidence**: Type checker validates changes across the entire codebase
- **Unified Astral toolchain**: Consistent tooling (ty + uv + ruff) reduces complexity
- **Performance**: ty is 10-60x faster than mypy without caching

### Negative

- **Initial development friction**: Strict enforcement may slow down rapid prototyping
- **Tool maturity**: ty is stable but still in active development
- **No plugin system**: ty does not support plugins like mypy

## Alternatives Considered

### mypy

- **Description**: The established standard for Python type checking
- **Why not chosen**: Too slow for pre-commit checks; violates the "fast feedback" goal

### pyright

- **Description**: Microsoft's fast type checker with excellent IDE integration
- **Why not chosen**: Less strict in detecting certain typing issues

---

## Notes

- ty was announced at PyCon US 2025 and is actively developed by Astral
- Performance benchmarks: 10-60x faster than mypy/Pyright without caching
- Part of Astral's vision for a unified, fast Python toolchain
- Install with: `uv add --dev ty`
- Run with: `uv run ty check`

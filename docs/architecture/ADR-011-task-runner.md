# ADR-011: Task Runner

**Status**: Accepted
**Date**: 2026-06-11

## Context

Developer commands need a single, memorable entry point (`just check` as the universal quality gate) — for the developer and for agents working in the repo. The TypeScript ecosystem default would be npm scripts.

## Decision

Use **just** as the task runner. The `justfile` defines the recipe vocabulary:

- `just install`, `just run`, `just test`, `just lint`, `just fmt`, `just typecheck`
- `just check` — lint + typecheck + test, the gate for considering any change complete

Recipes delegate to pnpm/node; npm `scripts` mirror the basics for editor integrations that expect them.

## Consequences

### Positive

- One workflow vocabulary — `just check` means the same thing for every contributor and agent, including in CLAUDE.md instructions
- Recipes with arguments (`just test -k ...`-style passthrough) and comments, which npm scripts handle poorly
- Language-agnostic: recipes that aren't node invocations (db inspection, service management) don't have to be shoehorned into package.json one-liners

### Negative

- One more host dependency: contributors need `just` installed (single binary, packaged everywhere)
- Mild duplication between the justfile and package.json scripts; the justfile is canonical, scripts are mirrors

## Alternatives Considered

- **npm scripts only**: zero extra dependencies, but awkward composition and argument passing
- **Makefile**: ubiquitous but with footguns (tabs, .PHONY, shell quirks) that just was designed to remove

# ADR-011: Task Runner

**Status**: Accepted
**Date**: 2026-06-11

## Context

The Python implementation standardized on `just` for developer commands (`just check` as the universal quality gate), and that muscle memory — for the developer and for agents working in the repo — is worth preserving across the rewrite. The TypeScript ecosystem default would be npm scripts.

## Decision

Carry over **just** as the task runner. The `justfile` defines the same recipe vocabulary as the Python repo:

- `just install`, `just run`, `just test`, `just lint`, `just fmt`, `just typecheck`
- `just check` — lint + typecheck + test, the gate for considering any change complete

Recipes delegate to pnpm/node; npm `scripts` mirror the basics for editor integrations that expect them.

## Consequences

### Positive

- Identical workflow across both Tachikoma repos — `just check` means the same thing everywhere, including in CLAUDE.md instructions for agents
- Recipes with arguments (`just test -k ...`-style passthrough) and comments, which npm scripts handle poorly
- Language-agnostic: recipes that aren't node invocations (db inspection, service management) don't have to be shoehorned into package.json one-liners

### Negative

- One more host dependency: contributors need `just` installed (single binary, packaged everywhere)
- Mild duplication between the justfile and package.json scripts; the justfile is canonical, scripts are mirrors

## Alternatives Considered

- **npm scripts only**: zero extra dependencies, but awkward composition and argument passing, and it would fork the workflow conventions between the two repos
- **Makefile**: ubiquitous but with footguns (tabs, .PHONY, shell quirks) that just was designed to remove

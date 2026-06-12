# ADR-012: Repository Structure

**Status**: Accepted
**Date**: 2026-06-11

## Context

The architecture makes every feature an extension behind a single `defineExtension` contract. That raises a packaging question: should extensions be separate packages (monorepo workspaces or independent repos) with the core as a published framework, or should everything live in one tree? The extension API is brand new and will churn heavily through the port milestones.

## Decision

**Single repository, single package**: the thin core under `src/core/` (or equivalent) and all first-party extensions in-tree under `src/extensions/*`, versioned and released together.

- First-party extensions use exactly the same `defineExtension` format and `AppContext` surface that third-party extensions will — no privileged internal APIs
- Third-party extensibility is **deferred to the plugins extension** (M5), which loads out-of-tree extensions through the same contract; until then the extension API is explicitly unstable
- No pnpm workspace packages for now; the uniform extension format keeps later extraction into packages cheap if ever warranted

## Consequences

### Positive

- The extension API can evolve freely during the port — breaking changes are atomic refactors across core and all consumers in one commit, with one CI run proving them
- First-party extensions double as living documentation and conformance tests for the contract third parties will eventually get
- One version, one changelog, one release pipeline; no cross-package dependency choreography

### Negative

- The core/extension boundary is enforced by discipline (and lint rules), not package boundaries — core must never import extension internals, and extensions must reach the core only through `AppContext`
- Third-party authors wait until the plugins extension lands and the API is declared stable
- The repo grows wide; mitigated by the strict one-extension-one-directory convention

## Alternatives Considered

- **pnpm monorepo with per-extension packages**: real boundaries, but constant version/workspace overhead while the API churns — premature before the contract stabilizes
- **Published core framework + external extension repos**: maximizes ecosystem optics, but freezes an API that hasn't survived a single feature port yet

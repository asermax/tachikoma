# ADR-013: Release Tooling

**Status**: Accepted
**Date**: 2026-06-13

## Context

Releases should be derived from conventional commits — version bump, changelog, npm publish, git tag, and GitHub release in one step — so that merging to the default branch is the only action a maintainer needs to take. Driving this from CI keeps the release reproducible (same Node, same lockfile-pinned toolchain) and removes the need for any maintainer to hold publish credentials locally.

## Decision

Use **semantic-release** run in CI on every push to `master`. The plugin chain is commit-analyzer → release-notes-generator → changelog → npm → git → github, configured in `.releaserc.json` against the `master` branch. `prepack` builds `dist` so any publish is always built.

The release runs from `.github/workflows/publish.yml`:

- **Quality gate first.** A `quality-gate` job runs lint (`biome check`), typecheck (`tsc --noEmit`), and tests (`vitest run`) before anything is published. The `release` job declares `needs: quality-gate`, so a failing check blocks the release.
- **Concurrency guard.** A `concurrency` group (`release-${{ github.ref }}`, `cancel-in-progress: false`) serializes releases so two pushes to `master` in quick succession cannot publish at the same time.
- **Credentials.** `@semantic-release/npm` authenticates to the registry via the `NPM_TOKEN` repo secret (exposed as both `NPM_TOKEN` and `NODE_AUTH_TOKEN`). The changelog/git/github plugins use the automatically provided `GITHUB_TOKEN` for the tag and GitHub release.

> **Required secret:** `NPM_TOKEN` must be configured as a repository secret (an npm automation token). Without it the npm publish step fails. `GITHUB_TOKEN` is provided automatically by Actions and needs no setup.

`just release` (`pnpm semantic-release`) remains available for local/manual invocation, but the canonical release path is CI on `master`.

The initial 2.0.0 release was created manually (version set, build, publish, `v2.0.0` tag + GitHub release): the codebase replaced an earlier implementation whose 1.x tags share this repository, and semantic-release would otherwise have started an unreachable-tag history at v1.0.0, colliding with them. With `v2.0.0` reachable on `master`, semantic-release picks it up as the baseline for every subsequent release.

## Consequences

### Positive

- Merging to `master` produces version, changelog, npm package, tag, and GitHub release, all derived from commit messages — no manual release step
- Releases are gated on a green lint + typecheck + test run
- Publish credentials live only in the repo secret store, not on maintainer machines
- Concurrent pushes cannot race to publish

### Negative

- Requires the `NPM_TOKEN` repo secret to be provisioned and kept valid
- Releases depend on commit message discipline (conventional commits)
- A misconfigured secret surfaces only at release time, in CI

## Alternatives Considered

- **Local-only `just release`**: removes the CI/secret dependency, but every maintainer needs npm auth and a GitHub token locally, the release is not reproducible across machines, and there is no enforced quality gate before publish
- **commit-and-tag-version**: local bump+changelog+tag, but no publish/GitHub release; replaced before first release
- **Tag-triggered GitHub Action publish**: splits the flow across local tagging and CI publishing, and double-publishes when combined with semantic-release's own tagging
- **changesets**: oriented at multi-package repos and PR-based flows

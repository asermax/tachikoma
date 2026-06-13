# ADR-013: Release Tooling

**Status**: Accepted
**Date**: 2026-06-13

## Context

Releases should be derived from conventional commits — version bump, changelog, npm publish, git tag, and GitHub release in one step — and runnable directly from a maintainer machine, matching the workflow already used by sibling projects (pi-save).

## Decision

Use **semantic-release** run locally (`just release`), with the same plugin chain as pi-save: commit-analyzer → release-notes-generator → changelog → npm → git → github, configured in `.releaserc.json` against the `master` branch. `prepack` builds `dist` so any publish is always built.

The initial 2.0.0 release was created manually (version set, build, publish, `v2.0.0` tag + GitHub release): the codebase replaced an earlier implementation whose 1.x tags share this repository, and semantic-release would otherwise have started an unreachable-tag history at v1.0.0, colliding with them. With `v2.0.0` reachable on `master`, semantic-release picks it up as the baseline for every subsequent release.

## Consequences

### Positive

- One command produces version, changelog, npm package, tag, and GitHub release, all derived from commit messages
- Same mental model and config as sibling projects
- No CI dependency or repository secrets needed to release

### Negative

- Requires local npm auth and a GitHub token at release time
- Releases depend on commit message discipline (conventional commits)

## Alternatives Considered

- **commit-and-tag-version**: local bump+changelog+tag, but no publish/GitHub release; replaced before first release
- **Tag-triggered GitHub Action publish**: needs repository secrets and splits the flow across local and CI; also double-publishes when combined with semantic-release's own tagging
- **changesets**: oriented at multi-package repos and PR-based flows

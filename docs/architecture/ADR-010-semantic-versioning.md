# ADR-010: Semantic Versioning via python-semantic-release

**Status**: Accepted
**Date**: 2026-03-29

## Context

Tachikoma is now packaged for PyPI distribution (DLT-024). Releases need to be versioned, tagged, and published automatically. Manual version management is error-prone and creates friction for releasing frequently.

The project already follows conventional commit format (`feat:`, `fix:`, `docs:`, etc.). An automated tool can analyze commit messages to determine the appropriate version bump, update the changelog, create git tags, and produce GitHub Releases — all without manual intervention.

Key requirements:
- Version bumps determined from commit messages (feat → minor, fix → patch)
- Changelog generated automatically
- Git tags and GitHub Releases created as part of the pipeline
- Integration with GitHub Actions (official action with well-defined outputs)
- Pre-1.0 behavior: breaking changes bump minor, not major

## Decision

Use **python-semantic-release (PSR) v10.x** with the `"conventional"` commit parser for automated semantic versioning.

Configuration in `pyproject.toml`:
- `commit_parser = "conventional"` (current parser; `"angular"` is deprecated, removed in v11)
- `version_toml = ["pyproject.toml:project.version"]` — stamps the version in pyproject.toml
- `build_command = "uv lock && uv build"` — regenerates lock file and builds artifacts during version bump
- `major_on_zero = false` — breaking changes bump minor while pre-1.0
- `tag_format = "v{version}"` — creates tags like `v0.2.0`
- Changelog in `update` mode — prepends new entries, preserves existing content

GitHub Actions integration:
- `python-semantic-release/python-semantic-release@v10.2.0` — version/tag/release action
- `python-semantic-release/publish-action@v10.2.0` — attaches dist artifacts to GitHub Release
- Publishing uses OIDC trusted publisher via `pypa/gh-action-pypi-publish`, eliminating stored secrets

## Consequences

### Positive

- **Zero-touch releases**: Push to master with conventional commits triggers everything automatically
- **Changelog generated**: CHANGELOG.md maintained without manual effort
- **Consistent versioning**: Version bumps follow semantic versioning rules deterministically
- **Clean GitHub Releases**: Each release has proper tag, changelog notes, and attached dist artifacts
- **Official GH Action**: Well-maintained action with output variables that chain cleanly to downstream steps

### Negative

- **Conventional commit discipline required**: All commits to master must follow the format (feat:, fix:, docs:, etc.) — currently enforced by culture, not tooling
- **PSR version coupling**: The GH Action version pin must be maintained; PSR's rapid release cadence may require periodic updates
- **Pre-1.0 limitation**: `major_on_zero = false` means breaking changes don't signal clearly in the version number until 1.0

## Alternatives Considered

### commitizen

- **Description**: Similar automation with conventional commit support, changelog generation, and bump commands
- **Why rejected**: PSR has better GitHub Actions integration with a dedicated publish-action and well-defined output variables (`released`, `tag`) for conditional workflow steps

### Manual versioning with bump2version

- **Description**: Explicit version commands, developer runs bump before release
- **Why rejected**: Requires manual intervention for every release; no changelog generation; doesn't integrate with CI/CD pipeline

---

## Notes

- PSR docs: https://python-semantic-release.readthedocs.io/en/latest/
- The `"conventional"` parser replaces the deprecated `"angular"` parser (removed in v11)
- The `build_command` runs during the PSR version step, not as a separate CI step — this ensures `uv.lock` and `dist/` are committed alongside the version bump

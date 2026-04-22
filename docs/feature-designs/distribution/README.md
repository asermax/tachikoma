# Distribution

## Overview

Design documents for package building, versioning, and publishing infrastructure.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [release-pipeline](release-pipeline.md) | CD pipeline: quality gates, semantic versioning, PyPI publishing | Current |
| [update-checker](update-checker.md) | Periodic PyPI version checking, user notification, and in-place upgrade | Current |

## Related Decisions

- ADR-001: Package Manager (uv)
- ADR-010: Semantic Versioning via python-semantic-release
- ADR-013: Key-Value Application State Table (dedup persistence)

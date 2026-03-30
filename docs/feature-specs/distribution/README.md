# Distribution

## Overview

Package building, versioning, and publishing infrastructure. Covers how Tachikoma is packaged for PyPI, how versions are determined from commit history, and how releases are published automatically.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [release-pipeline](release-pipeline.md) | CD pipeline: quality gates, semantic versioning, PyPI publishing | ✓ |

## Related Decisions

- ADR-001: Package Manager (uv)
- ADR-010: Semantic Versioning via python-semantic-release

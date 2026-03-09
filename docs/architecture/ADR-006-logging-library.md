# ADR-006: Logging Library

**Status**: Accepted
**Date**: 2026-03-09

## Context

We need a logging solution to detect issues in the agent during development and production. The logging library should be established early so logging is built into features from the start.

Requirements:
- Simple setup to minimize configuration overhead
- Good performance to avoid adding latency
- Built-in log rotation and management features
- Excellent debugging capabilities
- Production-ready and actively maintained

## Decision

Use **loguru** as the logging library for the project.

### Configuration

**Core settings:**
- **Log rotation**: 100 MB per file with 7 day retention
- **Compression**: Automatic gzip compression for rotated logs

**Log levels by environment:**
- **Development**: DEBUG level with color-coded console output
- **Production**: INFO level, structured format for parsing

## Consequences

### Positive

- **Minimal setup**: Single import, simple API, works out of the box with sensible defaults
- **Better performance**: 2-4x faster than standard library logging
- **Built-in features**: Automatic log rotation, retention policies, compression out of the box
- **Excellent debugging**: `@logger.catch` decorator captures full exception context
- **Color-coded output**: Easy to read during development
- **Production-ready**: Mature library (14K GitHub stars), stable API
- **Consistency**: Same library used in shinsenkyo — shared conventions across projects

### Negative

- **External dependency**: Requires installation
- **Less structured**: Not as sophisticated as structlog for JSON logs
- **Less fine-grained control**: Higher-level API means less customization than stdlib logging

## Alternatives Considered

### Standard library `logging`

- **Description**: Python's built-in logging module
- **Why not chosen**: Verbose setup requiring significant boilerplate

### Structlog

- **Description**: Specialized library for structured JSON logging
- **Why not chosen**: Steeper learning curve; overkill for current needs

---

## Notes

- Install with: `poetry add loguru`
- Documentation: https://loguru.readthedocs.io/en/stable/
- Future consideration: Can add structlog later if we need structured JSON logs for production monitoring

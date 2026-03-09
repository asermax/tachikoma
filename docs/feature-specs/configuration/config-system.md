# Configuration System

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A typed configuration system backed by a single TOML file at `~/.config/tachikoma/config.toml`. All tunable parameters live in this file with sensible defaults. On first run, a commented default config file is auto-generated so users can see what's configurable. The system validates all values at startup and provides clear error messages.

The `ANTHROPIC_API_KEY` is not managed by this system — the Claude SDK reads it natively from the environment. Only Tachikoma-specific parameters go in the config file.

## User Stories

- As a developer deploying Tachikoma, I want all tunable parameters managed in a single configuration file so that I can customize behavior without modifying code

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Clean separation between operational configuration and code |
| R1 | All parameters managed via a single TOML config file at `~/.config/tachikoma/config.toml` |
| R2 | Sensible defaults for all non-secret parameters |
| R3 | Startup validation with clear error messages for missing/invalid values |
| R4 | Auto-generate a commented default config file when none exists |
| R5 | Easy extensibility — adding new config parameters is low-friction |

## Behaviors

### Configuration Loading (R1, R2)

The system loads parameters from the TOML config file at startup, applying defaults for any unspecified values.

**Acceptance Criteria**:
- Given a valid TOML config file, when the application starts, then all parameters are loaded and available to components
- Given a config file with no `[workspace]` section, when loaded, then `workspace.path` defaults to `~/tachikoma`
- Given a config file with no `[agent]` section, when loaded, then `agent.model` defaults to `None` (SDK default) and `agent.allowed_tools` defaults to `["Read", "Glob", "Grep"]`
- Given a completely empty config file, when loaded, then all non-secret parameters use their defaults and the application starts successfully

### Startup Validation (R3)

The system validates all configuration values at startup, exiting with clear error messages on failure.

**Acceptance Criteria**:
- Given a config file with an invalid value (e.g. wrong type), when the application starts, then it exits with a clear error naming the field and expected type
- Given a config file with unknown keys, when loaded, then the unknown keys are silently ignored (forward-compatible)
- Given invalid TOML syntax, when the application starts, then it exits with a clear parse error
- Given the config file is not readable (permission denied), when the application starts, then it exits with a clear error
- Given the config path is not a regular file, when the application starts, then it exits with a clear error

### Auto-Generation (R4)

When no config file exists, the system creates a commented default file so users can discover what's configurable.

**Acceptance Criteria**:
- Given no config file exists, when the application starts, then a default config file is created with all parameters commented out and annotated
- Given no config directory exists, when the application starts, then the directory is created before writing the default config
- Given the config directory cannot be created (permission denied), when the application starts, then it exits with a clear error
- Given a config file already exists, when the application starts, then it is loaded as-is (never overwritten)

### Extensibility (R5)

Adding new configuration parameters is low-friction and backward-compatible.

**Acceptance Criteria**:
- Given a settings model with a new field that has a default value, when a config file without that field is loaded, then the new field uses its default without error

# Core Architecture

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The core agent loop: receive a user message, pass it to the Claude agent via the SDK, and stream the response back as domain events. Channels (REPL, Telegram, etc.) call a single programmatic entry point and consume a uniform event stream, decoupled from SDK internals.

## User Stories

- As a developer, I want a programmatic entry point that accepts a message and streams back domain events so that I can build channels without knowing SDK details
- As a developer, I want conversation context preserved across messages so that follow-up messages are coherent

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Core agent loop: send a user message, receive a streamed response as domain events |
| R1 | Project structure: pyproject.toml with dependencies, src package layout, entry point |
| R2 | Programmatic entry point (coordinator) that channels call to send messages and get streamed responses |
| R3 | Session lifecycle: connect and disconnect from the agent service, preserving conversation context across messages within a session. Optionally tracks sessions persistently via a session registry (see [sessions](sessions.md)) |
| R4 | Error handling: distinguish between transient failures that allow continued use and fatal failures that require stopping |
| R5 | Agent operates from workspace directory via SDK cwd option |
| R6 | Post-processing pipeline: on session close, run registered processors to analyze the completed conversation |

## Behaviors

### Message Processing (R0)

The coordinator receives a text message, forwards it to the SDK client, and yields domain events as the agent responds.

**Acceptance Criteria**:
- Given a user message, when passed to the coordinator, then the agent responds via the Claude model and the response streams as domain events
- Given a conversation in progress, when the user sends a follow-up message, then the agent has context from prior messages in the same session (R3)
- Given a conversation, when the user asks about files in the working directory, then the agent can explore and report on them

### Programmatic Entry Point (R2)

Channels interact with the agent through a single coordinator interface that returns an async event stream.

**Acceptance Criteria**:
- Given a channel implementation, when it calls the coordinator with a user message, then it receives an async iterator that yields domain events as the response streams
- Given the coordinator produces events, when a channel consumes them, then only meaningful domain events are surfaced (internal SDK messages are filtered)

### Session Lifecycle (R3)

The coordinator manages connection to the underlying agent service and maintains conversation context.

**Acceptance Criteria**:
- Given the coordinator enters its async context, then it connects to the agent service
- Given the coordinator exits its async context, then it disconnects from the agent service
- Given a new conversation starts, then a new session is created
- Given an active session, when subsequent messages arrive, then they use the same session
- Given a session registry is available, when the first message in a new conversation arrives, then a persistent session is created before the message is processed
- Given an active persistent session, when the agent produces a Result event, then the session's SDK metadata (session ID and transcript path) is populated
- Given an active persistent session, when the coordinator exits its async context, then the persistent session is closed
- Given a session registry operation fails, then the error is logged and the conversation continues uninterrupted

### Working Directory (R5)

The coordinator operates from the workspace directory, ensuring the agent's file operations are relative to the configured workspace path.

**Acceptance Criteria**:
- Given the agent starts, when the coordinator is created, then its working directory is set to the workspace path from configuration
- Given a custom workspace path is configured, when the agent starts, then the coordinator's working directory matches that custom path

### Startup Validation (R4)

Fatal errors at startup are caught before the main loop begins.

**Acceptance Criteria**:
- Given missing or invalid authentication, when the coordinator attempts to connect, then the process exits with a clear error message

### Post-Processing Pipeline Trigger (R6)

After a session closes, the coordinator triggers a registered post-processing pipeline that runs processors in parallel to analyze the completed conversation.

**Acceptance Criteria**:
- Given a session closes with a valid SDK session ID and a pipeline is registered, when the coordinator exits its async context, then the pipeline runs all registered processors in parallel
- Given a session closes without an SDK session ID, when the coordinator would trigger post-processing, then the pipeline is skipped and a warning is logged
- Given no active session exists, when the coordinator exits its async context, then the pipeline is not triggered
- Given a pipeline processor fails, when other processors are running, then the failing processor's error is logged and the others complete normally
- Given the pipeline itself fails, when the coordinator is shutting down, then the error is logged and shutdown continues (SDK disconnect proceeds)
- Given no pipeline is registered, when a session closes, then shutdown proceeds directly to SDK disconnect
- Given a pipeline is already running from a previous session close, when another session close triggers the pipeline, then the new run is serialized (awaits the previous one before starting)

### Error Recovery (R4)

Transient errors keep the conversation usable. Fatal errors signal channels to exit.

**Acceptance Criteria**:
- Given a transient connection error mid-stream, then an error event with `recoverable=True` is produced and the conversation remains usable; partial output remains visible
- Given an in-stream rate limit or server error, then an error event with `recoverable=True` is produced
- Given an in-stream authentication or billing error, then an error event with `recoverable=False` is produced

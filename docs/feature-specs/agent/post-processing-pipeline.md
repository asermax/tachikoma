# Post-Processing Pipeline

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A reusable, pluggable pipeline that runs registered processors after conversation end. The pipeline supports phased execution — processors declare which phase they run in, phases execute sequentially (`main → pre_finalize → finalize`), and processors within each phase run in parallel with error isolation. The pipeline is domain-agnostic; it knows nothing about what processors do.

A parallel concept — the `MessagePostProcessingPipeline` — follows a similar structural pattern (processor ABC, serialized execution, error isolation) but as a separate implementation with a distinct per-message processor interface that receives the active session, user message, and agent response. It has no phased execution. See [boundary detection](boundary-detection.md) for details.

## User Stories

- As a developer, I want a reusable pipeline so that any post-conversation processor can register without coupling to other processors
- As a developer, I want phased execution so that finalization tasks (like git commits) can run after all other processors complete

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Reusable pipeline that runs registered processors after session close |
| R1 | Phased execution — processors declare a phase at registration; phases run sequentially (`main → pre_finalize → finalize`), processors within a phase run in parallel |
| R2 | Error isolation — individual processor failures are logged but don't prevent other processors or subsequent phases from completing |
| R3 | Concurrent invocations serialized via lock |
| R4 | Shared processor interface (ABC) that is domain-agnostic and SDK-decoupled |
| R5 | Phase validation at registration — invalid phases rejected immediately |
| R6 | Convenience base class for prompt-driven processors that standardizes the fork pattern (DES-004) |
| R7 | Resumption-aware processing: processors receive session `last_resumed_at` and augment fork prompts to avoid re-extracting already-processed content |
| R8 | Pipeline tracks processing state: a transient `is_processing` flag prevents concurrent re-entry, and `mark_processed` is called on the session registry after all phases complete |
| R9 | Pipeline exposes `needs_processing(session, last_message_time)` to determine whether processing is needed (returns False when already processing or already processed since last message) |
| R10 | Sub-agents spawned by fork/query helpers declare explicit tool restrictions and allow-only permission rules via `dontAsk` mode; each processor defines the exact tools and paths its agent needs (DES-004) |
| R11 | Context summary distribution: before running phases, the pipeline builds a summary of session context entries and passes it to all processors via an `extra` dict so prompt-driven processors can avoid re-extracting information already captured in loaded memories or skills; skill entries include the skill's name, one-line description, and absolute directory path so processors can read skill files for dedup |
| R12 | Pipeline supports `unregister(processor)` for processor removal — identity-based removal, safe no-op when processor is not registered |
| R13 | Phase is a class attribute on `PostProcessor` (default `MAIN_PHASE`); `register()` reads the class attribute when no explicit `phase` argument is provided (sentinel-based backward compatibility) |
| R14 | Plugin-contributed post-processors participate in the pipeline identically to built-in processors — same error isolation, same phase execution, same context summary distribution |

## Behaviors

### Processor Registration (R0, R1, R5, R12, R13)

Processors declare their phase as a class attribute on `PostProcessor` (defaulting to `main`). The `register()` method reads the class attribute when no explicit `phase` argument is provided; explicit `phase` arguments take precedence for backward compatibility. Invalid phases are rejected at registration. Processors can be unregistered from the pipeline.

**Acceptance Criteria**:
- Given a `PostProcessor` subclass without an explicit `phase` override, when it is registered with the pipeline, then it defaults to the main phase
- Given a `PostProcessor` subclass with `phase = "pre_finalize"`, when it is registered without an explicit phase argument, then it is placed in the pre_finalize phase
- Given a processor is registered with an explicit `phase="finalize"` argument, when it is added, then the explicit argument takes precedence over the class attribute
- Given a processor is registered with an invalid phase, when `register()` is called, then a `ValueError` is raised listing valid phases
- Given multiple processors register for the same phase, when the pipeline runs, then they execute in parallel
- Given a processor that is registered in the pipeline, when `unregister(processor)` is called, then the processor is removed from its phase list
- Given a processor that is not registered in any phase, when `unregister(processor)` is called, then the call succeeds silently with no error

### Phased Execution (R1, R2)

The pipeline runs phases sequentially (`main → pre_finalize → finalize`). Within each phase, processors run in parallel. Failures in one phase do not prevent subsequent phases from running.

**Acceptance Criteria**:
- Given processors in main, pre_finalize, and finalize phases, when the pipeline runs, then main-phase processors complete before pre_finalize-phase processors start, and pre_finalize-phase processors complete before finalize-phase processors start
- Given a main-phase processor fails, when the finalize phase begins, then finalize-phase processors still run
- Given a phase has no registered processors, when the pipeline runs that phase, then it is skipped
- Given a processor fails, when other processors in the same phase are running, then they complete normally

### Serialization (R3)

Concurrent pipeline invocations are serialized to prevent interleaving.

**Acceptance Criteria**:
- Given a pipeline is already running, when another invocation arrives, then it waits for the first to complete before starting

### Shared Interface (R4)

The `PostProcessor` ABC defines the processor contract without SDK coupling.

**Acceptance Criteria**:
- Given a class implements `PostProcessor`, when it defines `process(session, *, extra=None)`, then it can register with the pipeline
- Given the `PostProcessor` ABC, then it has no dependency on the Claude Agent SDK

### Prompt-Driven Processor Base (R6)

A convenience base class standardizes the pattern for processors that fork the SDK session with a prompt (DES-004). Simple processors inherit `process()` from the base; complex processors override it for pre/post steps.

**Acceptance Criteria**:
- Given a subclass providing a prompt, when `process()` is called, then it forks the SDK session via `fork_and_consume()` with the configured prompt and working directory
- Given a subclass that overrides `process()`, when it calls `fork_and_consume()` directly, then it can add pre/post steps around the fork
- Given a subclass that overrides `process()`, when it calls `fork_and_consume()` with `mcp_servers`, then the forked agent has access to the provided MCP tools

### Resumption-Aware Processing (R7)

When a resumed session eventually closes, processors augment their fork prompts with a resumption boundary instruction to avoid re-extracting already-processed content.

**Acceptance Criteria**:
- Given a session with `last_resumed_at` set, when `PromptDrivenProcessor.process()` runs, then the fork prompt is augmented with a resumption boundary instruction via the shared `augment_prompt_for_resumption()` helper
- Given a session with `last_resumed_at` as None, when `PromptDrivenProcessor.process()` runs, then the fork prompt is used unchanged
- Given a subclass that overrides `process()`, when it calls `fork_and_consume()`, then it should also apply resumption augmentation via the shared `augment_prompt_for_resumption()` helper to maintain consistency

### Processing State Tracking (R8, R9)

The pipeline tracks whether it is currently executing and marks the session as processed after completion. A `needs_processing` method encapsulates the "should we run?" check.

**Acceptance Criteria**:
- Given the pipeline starts running, when `is_processing` is checked, then it returns True
- Given the pipeline finishes all phases, then `mark_processed` is called on the session registry
- Given the pipeline run fails unexpectedly (not individual processor failures), then `mark_processed` is not called
- Given `is_processing` is True, when `needs_processing` is checked, then it returns False
- Given `session.processed_at >= last_message_time`, when `needs_processing` is checked, then it returns False
- Given `session.processed_at < last_message_time`, when `needs_processing` is checked, then it returns True
- Given the pipeline finishes, when `is_processing` is checked, then it returns False (cleared in finally)

### Permission-Scoped Agents (R10)

Sub-agents spawned by processors declare explicit tool restrictions and allow-only permission rules. The `dontAsk` permission mode auto-denies any tool call not in the allow list.

**Acceptance Criteria**:
- Given a processor configured with tools and allow rules, when `fork_and_consume` constructs the agent options, then `dontAsk` mode and the allow rules are set instead of `bypassPermissions`
- Given a processor configured with allow rules restricting Edit/Write to a specific path, when its forked agent writes within that path, then the write succeeds
- Given a processor configured with allow rules restricting Edit/Write to a specific path, when its forked agent writes outside that path, then the write is auto-denied

### Context Summary Distribution (R11)

Before running phases, the pipeline loads session context entries from the registry, builds a concise summary (not full content), and passes it to all processors via an `extra` keyword argument on `process()`. Prompt-driven processors append the summary to their fork prompt with actionable instructions. Skill entries include the skill's name, one-line description, and absolute directory path so processors can read skill files for dedup; legacy entries lacking this metadata fall back to name-only rendering.

**Acceptance Criteria**:
- Given a session with loaded memory files and active skills, when the pipeline runs, then all prompt-driven processors receive a context summary listing those memories and skills
- Given a session with active skills that have enriched metadata, when the pipeline builds the context summary, then skill entries include the skill's name, one-line description, and absolute directory path
- Given a session with active skills that lack enriched metadata (legacy entries), when the pipeline builds the context summary, then skill entries fall back to name-only rendering without error
- Given a session with no context entries, when the pipeline runs, then `extra=None` is passed and processors behave exactly as before
- Given the registry fails to load context entries, when the pipeline runs, then processors run without a summary and no error is raised
- Given a loaded memory file that already captures a fact from the conversation, when the facts processor runs, then the summary instructs it to update the existing file rather than create a duplicate
- Given a non-prompt processor (git, projects, cleanup), when the pipeline runs, then it receives the `extra` dict but ignores it

### Plugin-Contributed Processors (R14)

Plugin post-processors participate in the pipeline identically to built-in processors. They are registered via event-driven listeners on `PluginInstalled`/`PluginRemoving` events, not directly by the coordinator. Plugin processors extending `PromptDrivenProcessor` automatically inherit DES-004 behavior (resumption augmentation, context summary injection, permission scoping).

**Acceptance Criteria**:
- Given a plugin post-processor registered in the `main` phase, when the pipeline runs, then it executes in parallel with built-in main-phase processors
- Given a plugin post-processor that raises during `process()`, when the pipeline runs, then the exception is caught by the existing error isolation and other processors complete normally
- Given a plugin post-processor extending `PromptDrivenProcessor`, when the pipeline runs, then it receives the context summary and applies resumption augmentation identically to built-in prompt-driven processors

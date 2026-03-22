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

## Behaviors

### Processor Registration (R0, R1, R5)

Processors register with the pipeline, optionally declaring a phase. Invalid phases are rejected at registration.

**Acceptance Criteria**:
- Given a processor is registered without a phase, when it is added to the pipeline, then it defaults to the main phase
- Given a processor is registered with `phase="finalize"`, when it is added, then it is placed in the finalize phase
- Given a processor is registered with `phase="pre_finalize"`, when it is added, then it is placed in the pre_finalize phase
- Given a processor is registered with an invalid phase, when `register()` is called, then a `ValueError` is raised listing valid phases
- Given multiple processors register for the same phase, when the pipeline runs, then they execute in parallel

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
- Given a class implements `PostProcessor`, when it defines `process(session)`, then it can register with the pipeline
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

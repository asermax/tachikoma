# Feature Specifications

Long-lived specifications describing **what** each part of Tachikoma does, kept current through delta reconciliation. Tachikoma is a thin core shell hosting first-party extensions (see [DES-001](../design/DES-001-unified-extension-api.md)); each feature below maps to a paired design under [../feature-designs/](../feature-designs/).

## Core & Conversation

| Feature | Description |
|---------|-------------|
| [core-shell](core-shell.md) | The shell around conversations: config, logging, SQLite, state store, event bus, scheduler, workspace layout, extension host |
| [conversation-loop](conversation-loop.md) | The message-handling cycle: inbox, inbound middleware, long-lived pi session, context gathering, streamed exchanges, phased post-processing |
| [agent-integration](agent-integration.md) | Embeds the pi agent SDK — session construction, model tiers, the SDK-free event adapter, and side-channel LLM runs |
| [repl](repl.md) | Minimal terminal channel: readline prompt, streamed inline rendering; the default channel |

## Sessions, Memory & Context

| Feature | Description |
|---------|-------------|
| [boundary-detection](boundary-detection.md) | Topical boundaries on the daily trunk — topic shifts, checkpointed side tangents (`/checkpoint`/`/back`), automatic detection, `/rollback`, and an `ask_branch` context-recovery tool |
| [foundational-context](foundational-context.md) | Composes the agent's identity from SOUL.md / USER.md (plus pi-native AGENTS.md) into the system prompt |
| [memory](memory.md) | Long-term memory as git-versioned markdown (episodic, topics) plus transcript archives, with a static index, trunk-close extraction and maintenance, and a one-time legacy-store migration |

## Skills, Workflows & Tasks

| Feature | Description |
|---------|-------------|
| [skills](skills.md) | Contributes workspace Agent Skills to pi's progressive disclosure, plus a `delegate_to_agent` tool for skill-bundled subagents |
| [skill-evolution](skill-evolution.md) | Nightly self-improvement of workspace skills — usage evidence becomes pattern pages and reviewable proposal branches |
| [workflows](workflows.md) | Multi-step processes as step-directory trees backed by a DB-persisted state machine that survives compaction and session boundaries |
| [tasks](tasks.md) | Scheduled work — cron/one-shot definitions firing as queued session turns or autonomous background runs |

## Channels, Projects & Git

| Feature | Description |
|---------|-------------|
| [telegram](telegram.md) | grammY-based Telegram bot channel with full media support and agent tools (send file, react, pin, inline prompts) |
| [projects](projects.md) | External repos as git submodules — register/list/deregister tools, startup sync, state injection, commit/push on close |
| [git-workspace](git-workspace.md) | Automatic git versioning of the workspace — init/sync, commit-on-close with generated messages, rebase-based push recovery |

## Processes, Notifications & Extensibility

| Feature | Description |
|---------|-------------|
| [detached-processes](detached-processes.md) | Dispatch, inspect, and terminate OS-level shell commands that outlive Tachikoma, with exit-watching and startup reconciliation |
| [notifications](notifications.md) | Routes notifications from any producer into the conversation as agent turns — severity maps to a delivery tier (urgent leads), batched into a digest |
| [self-update](self-update.md) | In-app updates: scheduled npm-registry version check + notify, an `upgrade_self` tool that installs and re-execs, and automatic rollback of a failed upgrade on the next boot |
| [external-extensions](external-extensions.md) | Loads out-of-tree extensions via the same `defineExtension` contract, with agent tools to install/update/list/uninstall |
| [migration](migration.md) | Startup adaptation of a workspace last used by a legacy install — non-destructive database/config/context/skill/task migration, self-detecting and idempotent |

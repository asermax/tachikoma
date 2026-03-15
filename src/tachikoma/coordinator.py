"""Coordinator: wraps ClaudeSDKClient and exposes a channel-facing async iterator API.

Channels call send_message() and consume the resulting AsyncIterator[AgentEvent].
The coordinator manages the SDK client lifecycle and transforms SDK messages into
domain events via the message adapter.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import TracebackType

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError, ProcessError
from claude_agent_sdk.types import AgentDefinition, PermissionMode, SystemPromptPreset
from loguru import logger

from tachikoma.adapter import adapt
from tachikoma.boundary import detect_boundary
from tachikoma.events import AgentEvent, Error, Result, TextChunk
from tachikoma.message_post_processing import MessagePostProcessingPipeline
from tachikoma.post_processing import PostProcessingPipeline
from tachikoma.pre_processing import PreProcessingPipeline, assemble_context
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry

_log = logger.bind(component="coordinator")


def _derive_transcript_path(sdk_session_id: str, cwd: Path | None) -> str:
    """Compute the Claude SDK transcript file path from an SDK session ID.

    Follows the Claude SDK convention:
        ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl

    where <sanitized-cwd> replaces "/" with "-" and strips the leading "-".

    Isolated here so that if the SDK changes its transcript storage format,
    only this one function needs updating.
    """
    effective_cwd = cwd or Path.cwd()
    sanitized = str(effective_cwd).replace("/", "-").lstrip("-")
    return str(Path.home() / ".claude" / "projects" / sanitized / f"{sdk_session_id}.jsonl")


class Coordinator:
    """Programmatic entry point for the agent.

    Usage::

        async with Coordinator(allowed_tools=["Read", "Glob", "Grep"]) as coord:
            async for event in coord.send_message("hello"):
                ...
    """

    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        model: str | None = None,
        cwd: Path | None = None,
        registry: SessionRegistry | None = None,
        system_prompt: str | None = None,
        pipeline: PostProcessingPipeline | None = None,
        pre_pipeline: PreProcessingPipeline | None = None,
        msg_pipeline: MessagePostProcessingPipeline | None = None,
        permission_mode: PermissionMode | None = None,
        env: dict[str, str] | None = None,
        on_status: Callable[[str], None] | None = None,
        agents: dict[str, AgentDefinition] | None = None,
    ) -> None:
        # Build SystemPromptPreset when system_prompt is provided
        sdk_system_prompt = None
        if system_prompt is not None:
            sdk_system_prompt = SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=system_prompt,
            )

        self._options = ClaudeAgentOptions(
            allowed_tools=allowed_tools or [],
            model=model,
            cwd=cwd,
            system_prompt=sdk_system_prompt,
            permission_mode=permission_mode,
            env=env or {},
            agents=agents,
        )
        self._cwd = cwd
        self._base_system_prompt = system_prompt
        self._client: ClaudeSDKClient | None = None
        self._registry = registry
        self._pipeline = pipeline
        self._pre_pipeline = pre_pipeline
        self._msg_pipeline = msg_pipeline
        self._on_status = on_status
        self._pending_steers: int = 0

        # Pending per-message post-processing task
        self._pending_msg_task: asyncio.Task[None] | None = None
        # Background session post-processing tasks from topic shifts
        self._background_tasks: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> "Coordinator":
        self._client = ClaudeSDKClient(self._options)
        await self._client.connect()
        _log.info("Connected to agent service")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Await any pending per-message post-processing task
        if self._pending_msg_task is not None:
            try:
                await self._pending_msg_task
            except Exception as exc:
                _log.exception(
                    "Pending per-message task failed: err={err}", err=str(exc)
                )
            finally:
                self._pending_msg_task = None

        # Capture active session reference BEFORE close_session (which clears _active_session)
        active: Session | None = None
        if self._registry is not None:
            active = await self._registry.get_active_session()

            if active is not None:
                try:
                    await self._registry.close_session(active.id)
                except Exception as exc:
                    _log.exception("Failed to close session on shutdown: err={err}", err=str(exc))

        # Run post-processing pipeline after session close, before SDK disconnect
        # Pipeline uses standalone query() which is independent of ClaudeSDKClient
        if active is not None and self._pipeline is not None:
            if active.sdk_session_id is not None:
                if self._on_status is not None:
                    try:
                        self._on_status("Processing memories...")
                    except Exception as exc:
                        _log.exception("Status callback failed: err={err}", err=str(exc))

                try:
                    await self._pipeline.run(active)
                except Exception as exc:
                    _log.exception(
                        "Post-processing pipeline failed: err={err}",
                        err=str(exc),
                    )
            else:
                _log.warning(
                    "Skipping post-processing: session has no SDK session ID"
                )

        # Await all background session post-processing tasks from topic shifts
        if self._background_tasks:
            results = await asyncio.gather(
                *self._background_tasks, return_exceptions=True
            )
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    _log.exception(
                        "Background task {i} failed: err={err}",
                        i=i,
                        err=str(result),
                    )
            self._background_tasks = []

        if self._client is not None:
            _log.info("Disconnecting from agent service")
            await self._client.disconnect()
            self._client = None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        """Send a user message and yield AgentEvents as the agent responds."""
        if self._client is None:
            raise RuntimeError("Coordinator is not connected. Use as an async context manager.")

        _log.debug("Message received: length={n}", n=len(text))

        # Await any pending per-message post-processing task before proceeding
        if self._pending_msg_task is not None:
            try:
                await self._pending_msg_task
            except Exception as exc:
                _log.exception(
                    "Pending per-message task failed, proceeding anyway: err={err}",
                    err=str(exc),
                )
            finally:
                self._pending_msg_task = None

        # Create a session if this is the first message in a new conversation
        active = None
        is_new_session = False

        if self._registry is not None:
            try:
                active = await self._registry.get_active_session()

                if active is None:
                    active = await self._registry.create_session()
                    is_new_session = True

            except Exception as exc:
                # Session tracking failures are logged but never crash the conversation
                _log.exception("Failed to create session: err={err}", err=str(exc))

        # Boundary detection: check if the message continues the current topic
        if active is not None and active.summary is not None and self._cwd is not None:
            try:
                continues = await detect_boundary(text, active.summary, self._cwd)
                if not continues:
                    _log.info("Topic shift detected, transitioning to new session")
                    await self._handle_transition(active)
                    # Re-fetch active session after transition
                    active = await self._registry.get_active_session()
                    is_new_session = True
            except Exception as exc:
                # Boundary detection failures default to continuation (fail-open)
                _log.exception(
                    "Boundary detection failed, proceeding as continuation: err={err}",
                    err=str(exc),
                )

        # Run pre-processing pipeline on first message of new session
        if is_new_session and self._pre_pipeline is not None:
            try:
                results = await self._pre_pipeline.run(text)
                if results:
                    text = assemble_context(results, text)
            except Exception as exc:
                _log.exception("Pre-processing failed: err={err}", err=str(exc))

        await self._client.query(text)

        # Accumulate response text for per-message post-processing
        response_chunks: list[str] = []

        try:
            async for sdk_message in self._client.receive_messages():
                done = False

                for event in adapt(sdk_message):
                    yield event

                    # Accumulate text chunks for per-message post-processing
                    if isinstance(event, TextChunk):
                        response_chunks.append(event.text)

                    # Populate SDK session metadata on the first Result event
                    if (
                        isinstance(event, Result)
                        and self._registry is not None
                        and active is not None
                        and event.session_id
                    ):
                        try:
                            transcript_path = _derive_transcript_path(
                                event.session_id, self._cwd
                            )
                            await self._registry.update_metadata(
                                session_id=active.id,
                                sdk_session_id=event.session_id,
                                transcript_path=transcript_path,
                            )
                        except Exception as exc:
                            _log.exception(
                                "Failed to update session metadata: err={err}", err=str(exc)
                            )

                    done = done or isinstance(event, Result)

                if done:
                    # Continue past Result when steering is active.
                    # Note: response_chunks accumulates text from all turns
                    # (initial + steered), but msg_pipeline only receives the
                    # initial user message. This is acceptable for boundary
                    # detection summaries — they still capture all response
                    # content, just without the steered user messages.
                    if self._pending_steers > 0:
                        self._pending_steers -= 1
                        done = False
                    else:
                        break

            # Trigger per-message post-processing after response completes
            if (
                self._msg_pipeline is not None
                and active is not None
                and self._registry is not None
            ):
                # Re-fetch session to get latest metadata (may have been updated)
                current_session = await self._registry.get_active_session()
                if current_session is not None:
                    response_text = "".join(response_chunks)
                    self._pending_msg_task = asyncio.create_task(
                        self._msg_pipeline.run(current_session, text, response_text)
                    )

            _log.debug("Response complete")

        except (CLIConnectionError, ProcessError) as exc:
            _log.error("Stream error (recoverable): err={err}", err=str(exc))
            yield Error(message=str(exc), recoverable=True)

    async def _handle_transition(self, previous_session: Session) -> None:
        """Handle session transition on topic shift.

        Closes the current session, fires async post-processing, resets the SDK
        client with the previous summary in system prompt, and creates a new session.
        """
        # Capture session snapshot
        session_snapshot = previous_session

        # Close session in registry
        if self._registry is not None:
            try:
                await self._registry.close_session(session_snapshot.id)
            except Exception as exc:
                _log.exception(
                    "Failed to close session during transition: err={err}", err=str(exc)
                )

        # Fire async session post-processing if session has sdk_session_id
        if session_snapshot.sdk_session_id is not None and self._pipeline is not None:
            task = asyncio.create_task(self._pipeline.run(session_snapshot))
            self._background_tasks.append(task)

            # Prune completed tasks to avoid unbounded growth
            self._background_tasks = [t for t in self._background_tasks if not t.done()]

        # Reset SDK client with previous summary in system prompt
        await self._reset_sdk_client(session_snapshot.summary)

        # Create new session in registry
        if self._registry is not None:
            try:
                await self._registry.create_session()
            except Exception as exc:
                _log.exception(
                    "Failed to create new session during transition: err={err}",
                    err=str(exc),
                )

    async def _reset_sdk_client(self, previous_summary: str | None) -> None:
        """Reset the SDK client with previous conversation summary in system prompt.

        Uses swap-on-success pattern: creates and connects new client before
        disconnecting the old one. If new client fails, old client is retained.
        """
        # Compose the new system prompt
        append_text = self._base_system_prompt or ""

        if previous_summary is not None:
            summary_section = f"""

# Previous Conversation
The user was previously discussing the following topic. This is provided
for brief context only — do not continue the previous conversation unless
the user explicitly refers back to it.

{previous_summary}"""
            # Strip leading newline if no base text precedes the summary
            append_text = (
                append_text + summary_section
                if append_text
                else summary_section[1:]
            )

        # Build new options
        new_system_prompt = None
        if append_text:
            new_system_prompt = SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=append_text,
            )

        new_options = ClaudeAgentOptions(
            allowed_tools=self._options.allowed_tools,
            model=self._options.model,
            cwd=self._options.cwd,
            system_prompt=new_system_prompt,
            permission_mode=self._options.permission_mode,
            env=self._options.env,
            agents=self._options.agents,
        )

        # Swap-on-success pattern
        old_client = self._client
        try:
            new_client = ClaudeSDKClient(new_options)
            await new_client.connect()
            # Success: swap clients
            self._client = new_client
            self._options = new_options

            if old_client is not None:
                try:
                    await old_client.disconnect()
                except Exception as disconnect_exc:
                    # Cleanup failure doesn't affect the new client
                    _log.exception(
                        "Failed to disconnect old client during swap: err={err}",
                        err=str(disconnect_exc),
                    )

            _log.info("SDK client reset with previous conversation summary")

        except Exception as exc:
            # Failure: keep old client (stale context but functional)
            _log.exception(
                "Failed to reset SDK client, keeping old client: err={err}",
                err=str(exc),
            )

    async def interrupt(self) -> None:
        """Interrupt the current agent response."""
        if self._client is not None:
            await self._client.interrupt()

    async def steer(self, text: str) -> None:
        """Inject a user message mid-stream via client.query().

        The message is queued by the CLI and processed after the current turn completes.
        The send_message() iteration continues yielding events for the steered message.
        """
        if self._client is None:
            raise RuntimeError("Coordinator is not connected. Use as an async context manager.")

        self._pending_steers += 1
        await self._client.query(text)
        _log.debug("Steered message queued: pending_steers={n}", n=self._pending_steers)

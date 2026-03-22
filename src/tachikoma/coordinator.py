"""Coordinator: per-message ClaudeSDKClient with resume-based session continuity.

Channels call send_message() and consume the resulting AsyncIterator[AgentEvent].
Each message exchange creates a fresh SDK client, using `resume` for conversation
continuity. Topic shifts simply start a new session without resume.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError, McpSdkServerConfig, ProcessError
from claude_agent_sdk.types import AgentDefinition, PermissionMode, SystemPromptPreset
from loguru import logger

from tachikoma.adapter import adapt
from tachikoma.boundary import BoundaryResult, SessionCandidate, detect_boundary
from tachikoma.events import AgentEvent, Error, Result, Status, TextChunk
from tachikoma.message_post_processing import MessagePostProcessingPipeline
from tachikoma.post_processing import PostProcessingPipeline
from tachikoma.pre_processing import (
    McpServerConfig,
    PreProcessingPipeline,
    assemble_context,
)
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

    Creates a fresh ClaudeSDKClient per message exchange. Conversation
    continuity is maintained via ``resume=sdk_session_id``. Topic shifts
    simply drop the resume ID so the next message starts a fresh session.

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
        cli_path: str | None = None,
        session_resume_window: int = 86400,
        mcp_servers: dict[str, McpSdkServerConfig] | None = None,
    ) -> None:
        # Store individual options for building ClaudeAgentOptions per message
        self._allowed_tools = allowed_tools or []
        self._model = model
        self._cwd = cwd
        self._cli_path = cli_path
        self._base_system_prompt = system_prompt
        self._permission_mode = permission_mode
        self._env = env or {}
        self._agents = agents
        self._mcp_servers = mcp_servers

        # Session resumption configuration
        self._session_resume_window = session_resume_window

        # Last message time tracking for idle gating
        self._last_message_time: datetime | None = None

        # SDK session tracking for resume
        self._sdk_session_id: str | None = None
        self._previous_summary: str | None = None
        self._bridging_context: str | None = None

        # MCP servers extracted from pre-processing pipeline (session-scoped)
        self._mcp_servers: dict[str, McpServerConfig] = {}

        # Active client (only set during send_message, None between messages)
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
        _log.info("Coordinator initialized")
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

        # Run post-processing pipeline after session close
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

    @property
    def last_message_time(self) -> datetime | None:
        """Return the timestamp of the last message exchange.

        Updated at the start of send_message() and after response completion.
        Used for idle gating of session tasks.
        """
        return self._last_message_time

    def _build_options(self, *, resume: str | None = None) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for a single message exchange.

        On topic shift, the previous conversation summary is appended to the
        system prompt for the first message of the new session only.

        On session resumption, bridging context is appended to provide context
        about conversations that occurred between the original session and now.
        """
        append_text = self._base_system_prompt or ""

        if self._previous_summary is not None:
            summary_section = f"""

# Previous Conversation
The user was previously discussing the following topic. This is provided
for brief context only — do not continue the previous conversation unless
the user explicitly refers back to it.

{self._previous_summary}"""

            append_text = (
                append_text + summary_section
                if append_text
                else summary_section[1:]
            )

            # Clear after first use — only the first message of the new session
            # needs the summary context
            self._previous_summary = None

        if self._bridging_context is not None:
            bridging_section = f"""

# Resumed Conversation
You are resuming a previous conversation that the user had earlier. The
following summaries describe conversations that occurred between then and now,
providing context for what the user has been doing in the meantime.

{self._bridging_context}"""

            append_text = (
                append_text + bridging_section
                if append_text
                else bridging_section[1:]
            )

            # Clear after first use — only the first message of the resumed session
            # needs the bridging context
            self._bridging_context = None

        sdk_system_prompt = None
        if append_text:
            sdk_system_prompt = SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=append_text,
            )

        options = ClaudeAgentOptions(
            allowed_tools=self._allowed_tools,
            model=self._model,
            cwd=self._cwd,
            cli_path=self._cli_path,
            system_prompt=sdk_system_prompt,
            permission_mode=self._permission_mode,
            env=self._env,
            agents=self._agents,
            resume=resume,
            mcp_servers=self._mcp_servers,
        )

        if self._mcp_servers:
            options.mcp_servers = self._mcp_servers

        return options

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        """Send a user message and yield AgentEvents as the agent responds.

        Creates a fresh ClaudeSDKClient for this exchange. Uses ``resume``
        for conversation continuity within the same session.
        """
        _log.debug("Message received: length={n}", n=len(text))

        # Track last message time for idle gating
        self._last_message_time = datetime.now(UTC)

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

        # Signal "Thinking..." when boundary detection or pre-processing will run
        will_detect_boundary = (
            active is not None and active.summary is not None and self._cwd is not None
        )
        will_preprocess = is_new_session and self._pre_pipeline is not None

        if will_detect_boundary or will_preprocess:
            yield Status(message="Thinking...")

        # Boundary detection: check if the message continues the current topic
        if will_detect_boundary:
            try:
                # Query recent closed sessions as candidates for resumption
                candidates: list[SessionCandidate] | None = None
                if self._registry is not None:
                    try:
                        now = datetime.now(UTC)
                        window = timedelta(seconds=self._session_resume_window)
                        recent = await self._registry.get_recent_closed(
                            before=now, window=window
                        )
                        candidates = [
                            SessionCandidate(id=s.id, summary=s.summary)
                            for s in recent
                        ]
                    except Exception as exc:
                        _log.exception(
                            "Failed to query resume candidates: err={err}",
                            err=str(exc),
                        )

                result: BoundaryResult = await detect_boundary(
                    text,
                    active.summary,
                    self._cwd,
                    candidates=candidates,
                    cli_path=self._cli_path,
                )
                if not result.continues:
                    _log.info(
                        "Topic shift detected, transitioning session"
                        " (resume_id={resume_id})",
                        resume_id=result.resume_session_id,
                    )
                    resumed = await self._handle_transition(
                        active, resume_session_id=result.resume_session_id
                    )
                    # Re-fetch active session after transition
                    active = await self._registry.get_active_session()
                    is_new_session = not resumed
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
                    # Merge mcp_servers from all results
                    merged: dict[str, McpServerConfig] = {}
                    for r in results:
                        if r.mcp_servers:
                            merged.update(r.mcp_servers)
                    self._mcp_servers = merged
                    text = assemble_context(results, text)

                    # Extract agents from pipeline results (DLT-021)
                    combined_agents: dict[str, AgentDefinition] = {}
                    for r in results:
                        if r.agents is not None:
                            combined_agents.update(r.agents)
                    self._agents = combined_agents if combined_agents else None
            except Exception as exc:
                _log.exception("Pre-processing failed: err={err}", err=str(exc))

        # Determine whether to resume the existing SDK session
        resume_id = self._sdk_session_id if not is_new_session else None

        # Build options and create a fresh client for this exchange
        options = self._build_options(resume=resume_id)
        response_chunks: list[str] = []

        try:
            async with ClaudeSDKClient(options) as client:
                self._client = client
                await client.query(text)

                # Process the response (receive_response stops at ResultMessage)
                async for sdk_message in client.receive_response():
                    for event in adapt(sdk_message):
                        yield event

                        if isinstance(event, TextChunk):
                            response_chunks.append(event.text)

                        if (
                            isinstance(event, Result)
                            and self._registry is not None
                            and active is not None
                            and event.session_id
                        ):
                            self._sdk_session_id = event.session_id
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
                                    "Failed to update session metadata: err={err}",
                                    err=str(exc),
                                )

                # Handle steered messages: each pending steer gets its own
                # receive_response() call which picks up the CLI's queued response
                while self._pending_steers > 0:
                    self._pending_steers -= 1
                    async for sdk_message in client.receive_response():
                        for event in adapt(sdk_message):
                            yield event

                            if isinstance(event, TextChunk):
                                response_chunks.append(event.text)

                self._client = None

        except (CLIConnectionError, ProcessError) as exc:
            self._client = None
            _log.error("Stream error (recoverable): err={err}", err=str(exc))
            yield Error(message=str(exc), recoverable=True)

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

        # Update last message time after response completes
        self._last_message_time = datetime.now(UTC)

        _log.debug("Response complete")

    async def _handle_transition(
        self, previous_session: Session, *, resume_session_id: str | None = None
    ) -> bool:
        """Handle session transition on topic shift.

        Closes the current session, fires async post-processing, then either:
        - Resumes a previous session if resume_session_id is provided and valid
        - Creates a fresh session if no resume or resume fails

        Returns:
            True if a previous session was resumed, False if a fresh session was created.
        """
        session_snapshot = previous_session

        # Capture close timestamp before registry close — needed for bridging context window
        closed_at = datetime.now(UTC)
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

        if resume_session_id is not None and self._registry is not None:
            try:
                reopened = await self._registry.reopen_session(resume_session_id)
                if reopened is not None:
                    # Resume successful — set SDK session ID and assemble bridging context
                    self._sdk_session_id = reopened.sdk_session_id

                    # Record the resumption event (best-effort)
                    await self._registry.record_resumption(
                        session_id=reopened.id,
                        previous_ended_at=closed_at,
                    )

                    # Assemble bridging context from intermediate sessions
                    await self._assemble_bridging_context(reopened, closed_at)

                    _log.info(
                        "Session resumed: session_id={id} sdk_session_id={sdk}",
                        id=reopened.id,
                        sdk=self._sdk_session_id,
                    )
                    return True

            except Exception as exc:
                _log.exception(
                    "Failed to resume session, falling back to fresh: err={err}",
                    err=str(exc),
                )

        # Fresh-session path: clear SDK session so next message starts fresh
        # Save the summary for injection into the new session's system prompt
        self._sdk_session_id = None
        self._agents = None
        self._previous_summary = session_snapshot.summary
        self._mcp_servers = {}

        # Create new session in registry
        if self._registry is not None:
            try:
                await self._registry.create_session()
            except Exception as exc:
                _log.exception(
                    "Failed to create new session during transition: err={err}",
                    err=str(exc),
                )

        return False

    async def _assemble_bridging_context(
        self, resumed_session: Session, closed_at: datetime
    ) -> None:
        """Assemble bridging context from intermediate sessions.

        Finds sessions that occurred between when the resumed session ended
        and now, concatenates their summaries to provide context.

        Args:
            resumed_session: The session being resumed.
            closed_at: When the previous session was closed (before resume).
        """
        if self._registry is None:
            self._bridging_context = None
            return

        try:
            now = datetime.now(UTC)
            intermediate = await self._registry.get_by_time_range(closed_at, now)

            # Filter: exclude the resumed session, include only those with summaries
            summaries = []
            for session in intermediate:
                if session.id != resumed_session.id and session.summary:
                    summaries.append(session.summary)

            if summaries:
                # Concatenate chronologically (earliest first - get_by_time_range returns DESC)
                self._bridging_context = "\n\n".join(reversed(summaries))
                _log.debug(
                    "Bridging context assembled: session_count={n}",
                    n=len(summaries),
                )
            else:
                self._bridging_context = None

        except Exception as exc:
            _log.exception(
                "Failed to assemble bridging context: err={err}",
                err=str(exc),
            )
            self._bridging_context = None

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

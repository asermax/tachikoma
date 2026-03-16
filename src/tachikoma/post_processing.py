"""Post-processing pipeline for running processors after conversation end.

Provides a reusable, pluggable pipeline that runs PostProcessor instances
in parallel with error isolation. Used by memory extraction processors and
other post-conversation handlers.
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import (
    McpHttpServerConfig,
    McpSdkServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
)
from loguru import logger

from tachikoma.sessions.model import Session

_log = logger.bind(component="post_processing")

# Fixed phase identifiers — validated at registration
MAIN_PHASE = "main"
FINALIZE_PHASE = "finalize"
_VALID_PHASES = frozenset({MAIN_PHASE, FINALIZE_PHASE})


class PostProcessor(ABC):
    """Abstract base class for post-processing handlers.

    Subclasses implement process() to perform their specific extraction
    or update logic. The ABC defines only the interface contract — no
    SDK coupling is inherited.
    """

    @abstractmethod
    async def process(self, session: Session) -> None:
        """Process a closed session.

        Args:
            session: The closed session with sdk_session_id for forking.
        """
        ...


class PromptDrivenProcessor(PostProcessor):
    """Base class for processors that fork the SDK session with a prompt.

    Simple processors that just need to send a prompt and let the agent
    manage files can inherit from this class and only provide a prompt
    constant. The base class handles storing prompt/cwd and implementing
    process() via fork_and_consume().

    Subclasses needing pre/post steps should override process() entirely
    and call fork_and_consume() directly (e.g., CoreContextProcessor).

    See DES-004 for the pattern documentation.
    """

    def __init__(self, prompt: str, cwd: Path, cli_path: str | None = None) -> None:
        """Initialize the processor.

        Args:
            prompt: The prompt to send to the forked agent.
            cwd: The workspace directory for the forked agent.
            cli_path: Optional path to the Claude CLI binary.
        """
        self._prompt = prompt
        self._cwd = cwd
        self._cli_path = cli_path

    async def process(self, session: Session) -> None:
        """Process by forking the SDK session with the configured prompt.

        Args:
            session: The closed session to process.
        """
        await fork_and_consume(session, self._prompt, self._cwd, cli_path=self._cli_path)


class PostProcessingPipeline:
    """Runs registered PostProcessor instances in parallel with error isolation.

    Usage::

        pipeline = PostProcessingPipeline()
        pipeline.register(EpisodicProcessor(cwd))
        pipeline.register(FactsProcessor(cwd))
        await pipeline.run(session)

    Individual processor failures are logged but don't prevent other
    processors from completing.
    """

    # Phase execution order
    _phase_order = [MAIN_PHASE, FINALIZE_PHASE]

    def __init__(self) -> None:
        # Pre-populate phases so register() can append without KeyError
        self._phases: dict[str, list[PostProcessor]] = {p: [] for p in _VALID_PHASES}
        self._lock = asyncio.Lock()

    def register(self, processor: PostProcessor, phase: str = MAIN_PHASE) -> None:
        """Register a processor to run on pipeline execution.

        Args:
            processor: The processor to register.
            phase: The phase to run this processor in. Must be "main" or "finalize".
                Defaults to "main" for backward compatibility.

        Raises:
            ValueError: If phase is not a valid phase identifier.
        """
        if phase not in _VALID_PHASES:
            valid_list = ", ".join(sorted(_VALID_PHASES))
            raise ValueError(f"Invalid phase '{phase}'. Valid phases: {valid_list}")
        self._phases[phase].append(processor)

    async def run(self, session: Session) -> None:
        """Run all registered processors in sequential phases.

        Phases run in order (main → finalize). Within each phase,
        processors run in parallel. Acquires an internal lock to serialize
        concurrent invocations.

        Individual processor failures are logged per DES-002 but don't
        propagate or prevent subsequent phases from running.
        """
        async with self._lock:
            for phase in self._phase_order:
                processors = self._phases[phase]
                if not processors:
                    continue

                results = await asyncio.gather(
                    *[p.process(session) for p in processors],
                    return_exceptions=True,
                )

                for processor, result in zip(processors, results, strict=True):
                    if isinstance(result, Exception):
                        _log.exception(
                            "Processor failed: processor={name} phase={phase} err={err}",
                            name=processor.__class__.__name__,
                            phase=phase,
                            err=str(result),
                        )


async def fork_and_consume(
    session: Session,
    prompt: str,
    cwd: Path,
    mcp_servers: dict[
        str,
        McpStdioServerConfig | McpSSEServerConfig | McpHttpServerConfig | McpSdkServerConfig,
    ]
    | None = None,
    cli_path: str | None = None,
) -> None:
    """Fork the SDK session and consume the agent's response.

    Creates a forked session using the standalone query() function,
    which operates independently of the coordinator's ClaudeSDKClient.
    The forked agent has full workspace access.

    Args:
        session: The session to fork (must have sdk_session_id).
        prompt: The extraction prompt to send to the forked agent.
        cwd: The working directory for the forked agent.
        mcp_servers: Optional MCP servers to provide to the forked agent.
            Can include in-process SDK MCP servers (from create_sdk_mcp_server())
            or external server configs.

    Raises:
        RuntimeError: If session has no sdk_session_id.
        Propagates: SDK errors from the query() call.
    """
    if session.sdk_session_id is None:
        raise RuntimeError(
            f"Cannot fork session {session.id}: no sdk_session_id available"
        )

    options = ClaudeAgentOptions(
        cwd=cwd,
        cli_path=cli_path,
        resume=session.sdk_session_id,
        fork_session=True,
        permission_mode="bypassPermissions",
    )

    if mcp_servers is not None:
        options.mcp_servers = mcp_servers

    # Fully consume the async iterator to ensure the forked session ends cleanly
    async for _ in query(prompt=prompt, options=options):
        pass

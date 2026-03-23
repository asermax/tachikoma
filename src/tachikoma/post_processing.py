"""Post-processing pipeline for running processors after conversation end.

Provides a reusable, pluggable pipeline that runs PostProcessor instances
in parallel with error isolation. Used by memory extraction processors and
other post-conversation handlers.
"""

import asyncio
from abc import ABC, abstractmethod

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import (
    McpHttpServerConfig,
    McpSdkServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
)
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.sessions.model import Session

_log = logger.bind(component="post_processing")

# Fixed phase identifiers — validated at registration
MAIN_PHASE = "main"
PRE_FINALIZE_PHASE = "pre_finalize"
FINALIZE_PHASE = "finalize"
_VALID_PHASES = frozenset({MAIN_PHASE, PRE_FINALIZE_PHASE, FINALIZE_PHASE})


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

    def __init__(self, prompt: str, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            prompt: The prompt to send to the forked agent.
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        self._prompt = prompt
        self._agent_defaults = agent_defaults
        self._cwd = agent_defaults.cwd

    async def process(self, session: Session) -> None:
        """Process by forking the SDK session with the configured prompt.

        If the session was resumed from a previous conversation (indicated by
        last_resumed_at), an augmentation is appended to the prompt to provide
        context to the forked agent.

        Args:
            session: The closed session to process.
        """
        name = self.__class__.__name__
        _log.info("Processor started: processor={name}", name=name)

        prompt = augment_prompt_for_resumption(self._prompt, session)
        await fork_and_consume(session, prompt, self._agent_defaults)
        _log.info("Processor completed: processor={name}", name=name)


def augment_prompt_for_resumption(prompt: str, session: Session) -> str:
    """Append resumption awareness to a prompt if the session was resumed."""
    if session.last_resumed_at is None:
        return prompt

    return (
        f"{prompt}\n\n"
        f"IMPORTANT: This session was resumed from a previous "
        f"conversation at {session.last_resumed_at}. The user is "
        f"returning to a topic they discussed earlier. Keep this "
        f"context in mind when processing."
    )


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
    _phase_order = [MAIN_PHASE, PRE_FINALIZE_PHASE, FINALIZE_PHASE]

    def __init__(self) -> None:
        # Pre-populate phases so register() can append without KeyError
        self._phases: dict[str, list[PostProcessor]] = {p: [] for p in _VALID_PHASES}
        self._lock = asyncio.Lock()

    def register(self, processor: PostProcessor, phase: str = MAIN_PHASE) -> None:
        """Register a processor to run on pipeline execution.

        Args:
            processor: The processor to register.
            phase: The phase to run this processor in. Must be "main", "pre_finalize",
                or "finalize".
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

        Phases run in order (main → pre_finalize → finalize). Within each phase,
        processors run in parallel. Acquires an internal lock to serialize
        concurrent invocations.

        Individual processor failures are logged per DES-002 but don't
        propagate or prevent subsequent phases from running.
        """
        async with self._lock:
            _log.info("Pipeline started: session={sid}", sid=session.id[:8])

            for phase in self._phase_order:
                processors = self._phases[phase]
                if not processors:
                    continue

                names = [p.__class__.__name__ for p in processors]
                _log.info(
                    "Phase started: phase={phase} processors={names}",
                    phase=phase, names=names,
                )

                results = await asyncio.gather(
                    *[p.process(session) for p in processors],
                    return_exceptions=True,
                )

                for processor, result in zip(processors, results, strict=True):
                    if isinstance(result, BaseException):
                        _log.exception(
                            "Processor failed: processor={name} phase={phase} err={err}",
                            name=processor.__class__.__name__,
                            phase=phase,
                            err=str(result),
                        )

                _log.info("Phase completed: phase={phase}", phase=phase)

            _log.info("Pipeline completed: session={sid}", sid=session.id[:8])


async def fork_and_consume(
    session: Session,
    prompt: str,
    agent_defaults: AgentDefaults,
    mcp_servers: dict[
        str,
        McpStdioServerConfig | McpSSEServerConfig | McpHttpServerConfig | McpSdkServerConfig,
    ]
    | None = None,
) -> None:
    """Fork the SDK session and consume the agent's response.

    Creates a forked session using the standalone query() function,
    which operates independently of the coordinator's ClaudeSDKClient.
    The forked agent has full workspace access.

    Args:
        session: The session to fork (must have sdk_session_id).
        prompt: The extraction prompt to send to the forked agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).
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
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        resume=session.sdk_session_id,
        fork_session=True,
        permission_mode="bypassPermissions",
    )

    if mcp_servers is not None:
        options.mcp_servers = mcp_servers

    # Fully consume the async iterator to ensure the forked session ends cleanly
    async for _ in query(prompt=prompt, options=options):
        pass

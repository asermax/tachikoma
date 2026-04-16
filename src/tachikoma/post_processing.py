"""Post-processing pipeline for running processors after conversation end.

Provides a reusable, pluggable pipeline that runs PostProcessor instances
in parallel with error isolation. Used by memory extraction processors and
other post-conversation handlers.
"""

import asyncio
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    McpHttpServerConfig,
    McpSdkServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    SystemPromptPreset,
)
from loguru import logger

from tachikoma.adapter import sanitize_text
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.sdk_query import stderr_aware_query
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry

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

    def __init__(
        self,
        prompt: str,
        agent_defaults: AgentDefaults,
        tools: list[str] | None = None,
        allow: list[str] | None = None,
        pre_tool_use_hooks: list[HookMatcher] | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize the processor.

        Args:
            prompt: The prompt to send to the forked agent.
            agent_defaults: Common SDK options (cwd, cli_path, env).
            tools: Optional tool restriction list for the forked agent.
            allow: Optional allow-only permission rules for path scoping.
                When provided (along with tools), the forked agent uses
                ``dontAsk`` mode instead of ``bypassPermissions``.
            pre_tool_use_hooks: Optional PreToolUse hook matchers for
                programmatic enforcement (e.g. Bash command gating via
                :func:`make_bash_gate_hook`).
            model: Optional model override for the forked agent. When None
                (the default), the fork inherits the parent session's model.
        """
        self._prompt = prompt.replace("$WORKSPACE", str(agent_defaults.cwd))
        self._agent_defaults = agent_defaults
        self._cwd = agent_defaults.cwd
        self._tools = tools
        self._allow = allow
        self._pre_tool_use_hooks = pre_tool_use_hooks
        self._model = model

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
        await fork_and_consume(
            session,
            prompt,
            self._agent_defaults,
            tools=self._tools,
            allow=self._allow,
            pre_tool_use_hooks=self._pre_tool_use_hooks,
            model=self._model,
        )
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

    Manages processing state: a transient ``is_processing`` flag prevents
    concurrent re-entry, and ``mark_processed`` is called on the session
    registry after all phases complete.  The ``needs_processing`` method
    encapsulates the "should we run?" check used by both the idle loop
    and shutdown.

    Usage::

        pipeline = PostProcessingPipeline(registry)
        pipeline.register(EpisodicProcessor(cwd))
        pipeline.register(FactsProcessor(cwd))
        await pipeline.run(session)

    Individual processor failures are logged but don't prevent other
    processors from completing.
    """

    # Phase execution order
    _phase_order = [MAIN_PHASE, PRE_FINALIZE_PHASE, FINALIZE_PHASE]

    def __init__(self, registry: SessionRegistry) -> None:
        # Pre-populate phases so register() can append without KeyError
        self._phases: dict[str, list[PostProcessor]] = {p: [] for p in _VALID_PHASES}
        self._lock = asyncio.Lock()
        self._registry = registry
        self._is_processing = False

    @property
    def is_processing(self) -> bool:
        """Whether the pipeline is currently executing."""
        return self._is_processing

    def needs_processing(self, session: Session, last_message_time: datetime | None) -> bool:
        """Whether the session has unprocessed content.

        Returns False if the pipeline is already running or if
        ``session.processed_at`` is at or after ``last_message_time``
        (meaning all content has already been processed).
        """
        if self._is_processing:
            return False

        return not (
            session.processed_at is not None
            and last_message_time is not None
            and session.processed_at >= last_message_time
        )

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

        Sets ``is_processing`` before acquiring the lock (for immediate
        caller visibility), runs phases in order (main → pre_finalize →
        finalize) with processors parallel within each phase, then marks
        the session as processed via the registry.

        Individual processor failures are logged per DES-002 but don't
        propagate or prevent subsequent phases from running.
        """
        self._is_processing = True

        try:
            async with self._lock:
                _log.info("Pipeline started: session={sid}", sid=session.id[:8])

                for phase in self._phase_order:
                    processors = self._phases[phase]
                    if not processors:
                        continue

                    names = [p.__class__.__name__ for p in processors]
                    _log.info(
                        "Phase started: phase={phase} processors={names}",
                        phase=phase,
                        names=names,
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

                try:
                    await self._registry.mark_processed(session.id)
                except Exception as exc:
                    _log.exception(
                        "Failed to mark session as processed: session={sid} err={err}",
                        sid=session.id[:8],
                        err=str(exc),
                    )

        finally:
            self._is_processing = False


def abs_rule(tool: str, path: Path) -> str:
    """Build an absolute-path permission rule.

    Uses the ``//`` prefix (absolute filesystem path) so the rule matches
    regardless of the CLI's resolved working directory.

    Args:
        tool: Tool name (e.g. ``"Read"``, ``"Edit"``, ``"Write"``).
        path: Absolute directory path to allow.

    Returns:
        Rule string like ``"Write(//home/user/workspace/memories/episodic/**)"``
    """
    return f"{tool}(//{str(path.resolve())[1:]}/**)"


# Regex for splitting compound Bash commands by shell operators.
# Order matters: && and || (two-char) are tried before | and ; (one-char).
_COMPOUND_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||[;|])\s*")


def make_bash_gate_hook(allowed_prefixes: list[str]) -> HookMatcher:
    """Create a PreToolUse hook that restricts Bash to specific command prefixes.

    Permission allow rules like ``Bash(git *)`` are not reliably enforced
    by the CLI (known upstream issue). This hook provides programmatic
    enforcement by inspecting the command string before execution.

    Compound commands (joined by ``&&``, ``||``, ``|``, or ``;``) are split
    and each sub-command is checked independently. If any sub-command does
    not match an allowed prefix, the entire command is denied.

    The splitting is intentionally simple — it does not parse shell quoting.
    This is a conservative security tradeoff: commands with shell operators
    inside quoted strings will be incorrectly split, but this is unlikely
    in practice for the constrained agents that use this hook.

    Args:
        allowed_prefixes: Command prefixes to allow (e.g. ``["git "]``).
            Each sub-command must exactly match a command name, or start
            with a command name followed by a space (for arguments).

    Returns:
        A ``HookMatcher`` targeting the Bash tool with one hook callback.
    """

    # Build a single regex from the prefix list: matches exact command name
    # or command name followed by a space and arguments.
    # Names are deduplicated and sorted longest-first for correct alternation.
    unique_names = sorted(
        {re.escape(p.rstrip()) for p in allowed_prefixes},
        key=lambda s: len(s),
        reverse=True,
    )
    alts = "|".join(unique_names)
    pattern = rf"^(?:{alts})($| .*)"
    _allowed_re = re.compile(pattern)

    async def _hook(
        input: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        command = input.get("tool_input", {}).get("command", "")

        # Split compound commands by shell operators
        parts = _COMPOUND_SPLIT_RE.split(command)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if _allowed_re.match(part):
                continue

            _log.warning(
                "Bash denied by hook: command={cmd} denied_part={part} allowed_prefixes={prefixes}",
                cmd=command[:80],
                part=part[:80],
                prefixes=allowed_prefixes,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Sub-command '{part}' not in allowed prefixes {allowed_prefixes}"
                    ),
                },
            }

        # All sub-commands passed
        return {}

    return HookMatcher(matcher="Bash", hooks=[_hook])


# Utility-only Bash prefixes shared by processors that need filesystem inspection
# commands but not git. Used by memory processors and CoreContextProcessor.
UTILITY_BASH_PREFIXES = [
    "ls ",
    "find ",
    "file ",
    "echo ",
    "date ",
    "cat ",
    "grep ",
    "head ",
    "tail ",
    "wc ",
    "stat ",
    "cd",
    "pwd",
]

UTILITY_BASH_HOOK = make_bash_gate_hook(UTILITY_BASH_PREFIXES)


def build_permissions_settings(allow: list[str]) -> str:
    """Build a settings JSON string with allow-only permission rules.

    Args:
        allow: List of permission allow rules. Use :func:`abs_rule` for
            path-scoped rules to ensure absolute paths.

    Returns:
        JSON string suitable for ``ClaudeAgentOptions.settings``.
    """
    return json.dumps({"permissions": {"allow": allow}})


async def fork_and_consume(
    session: Session,
    prompt: str,
    agent_defaults: AgentDefaults,
    mcp_servers: dict[
        str,
        McpStdioServerConfig | McpSSEServerConfig | McpHttpServerConfig | McpSdkServerConfig,
    ]
    | None = None,
    system_prompt_append: str | None = None,
    tools: list[str] | None = None,
    allow: list[str] | None = None,
    pre_tool_use_hooks: list[HookMatcher] | None = None,
    model: str | None = None,
) -> None:
    """Fork the SDK session and consume the agent's response.

    Creates a forked session using the standalone query() function,
    which operates independently of the coordinator's ClaudeSDKClient.

    When ``tools`` and ``allow`` are provided, the forked agent uses
    ``dontAsk`` permission mode with explicit allow rules instead of
    ``bypassPermissions``. This restricts the agent to only the
    specified tools and paths.

    Args:
        session: The session to fork (must have sdk_session_id).
        prompt: The extraction prompt to send to the forked agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).
        mcp_servers: Optional MCP servers to provide to the forked agent.
            Can include in-process SDK MCP servers (from create_sdk_mcp_server())
            or external server configs.
        system_prompt_append: Optional text to append to the system prompt.
            When provided, the forked agent receives this context in addition
            to the default Claude Code system prompt.
        tools: Optional tool restriction list for the forked agent.
        allow: Optional allow-only permission rules for path scoping.
            When provided (along with tools), the forked agent uses
            ``dontAsk`` mode instead of ``bypassPermissions``.
        pre_tool_use_hooks: Optional PreToolUse hook matchers for
            programmatic enforcement (e.g. Bash command gating).
        model: Optional model override for the forked agent. When None
            (the default), the fork inherits the parent session's model.
            Pass a model alias (e.g. ``"haiku"``) to downgrade the fork
            for cheap mechanical tasks.

    Raises:
        RuntimeError: If session has no sdk_session_id.
        Propagates: SDK errors from the query() call.
    """
    if session.sdk_session_id is None:
        raise RuntimeError(f"Cannot fork session {session.id}: no sdk_session_id available")

    _log.debug("Forking session: sdk_session_id={sid}", sid=session.sdk_session_id[:8])

    options = ClaudeAgentOptions(
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        resume=session.sdk_session_id,
        fork_session=True,
    )

    if model is not None:
        options.model = model

    if tools is not None and allow is not None:
        options.tools = tools
        options.settings = build_permissions_settings(allow)
        options.extra_args = {"permission-mode": "dontAsk"}
    else:
        options.permission_mode = "bypassPermissions"

    if pre_tool_use_hooks is not None:
        options.hooks = {"PreToolUse": pre_tool_use_hooks}

    if mcp_servers is not None:
        options.mcp_servers = mcp_servers

    if system_prompt_append is not None:
        options.system_prompt = SystemPromptPreset(
            type="preset",
            preset="claude_code",
            append=system_prompt_append,
        )

    # Fully consume the async iterator to ensure the forked session ends cleanly
    async for _ in stderr_aware_query(prompt=prompt, options=options):
        pass

    _log.debug("Fork completed: sdk_session_id={sid}", sid=session.sdk_session_id[:8])


async def fork_and_capture(
    session: Session,
    prompt: str,
    agent_defaults: AgentDefaults,
    system_prompt_append: str | None = None,
    tools: list[str] | None = None,
    allow: list[str] | None = None,
    pre_tool_use_hooks: list[HookMatcher] | None = None,
    model: str | None = None,
) -> str:
    """Fork the SDK session and capture the agent's text response.

    Same fork pattern as fork_and_consume but captures and returns the
    concatenated text content from all content blocks in the response
    stream. Returns empty string if no text is produced.

    Args:
        session: The session to fork (must have sdk_session_id).
        prompt: The prompt to send to the forked agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).
        system_prompt_append: Optional text to append to the system prompt.
            When provided, the forked agent receives this context in addition
            to the default Claude Code system prompt.
        tools: Optional tool restriction list for the forked agent.
        allow: Optional allow-only permission rules for path scoping.
        pre_tool_use_hooks: Optional PreToolUse hook matchers.
        model: Optional model override for the forked agent. When None
            (the default), the fork inherits the parent session's model.

    Returns:
        Concatenated text from all content blocks in the response.

    Raises:
        RuntimeError: If session has no sdk_session_id.
        Propagates: SDK errors from the query() call.
    """
    if session.sdk_session_id is None:
        raise RuntimeError(f"Cannot fork session {session.id}: no sdk_session_id available")

    _log.debug("Forking session for capture: sdk_session_id={sid}", sid=session.sdk_session_id[:8])

    options = ClaudeAgentOptions(
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        resume=session.sdk_session_id,
        fork_session=True,
    )

    if model is not None:
        options.model = model

    if tools is not None and allow is not None:
        options.tools = tools
        options.settings = build_permissions_settings(allow)
        options.extra_args = {"permission-mode": "dontAsk"}
    else:
        options.permission_mode = "bypassPermissions"

    if pre_tool_use_hooks is not None:
        options.hooks = {"PreToolUse": pre_tool_use_hooks}

    if system_prompt_append is not None:
        options.system_prompt = SystemPromptPreset(
            type="preset",
            preset="claude_code",
            append=system_prompt_append,
        )

    # Fully consume the async iterator per DES-005, capturing text content
    chunks: list[str] = []

    async for message in stderr_aware_query(prompt=prompt, options=options):
        content = getattr(message, "content", None)
        if content is not None:
            for block in content:
                if hasattr(block, "text"):
                    chunks.append(sanitize_text(block.text))

    result = "".join(chunks)
    _log.debug(
        "Fork capture completed: sdk_session_id={sid}, text_length={length}",
        sid=session.sdk_session_id[:8],
        length=len(result),
    )

    return result

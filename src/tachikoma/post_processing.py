"""Post-processing pipeline for running processors after conversation end.

Provides a reusable, pluggable pipeline that runs PostProcessor instances
in parallel with error isolation. Used by memory extraction processors and
other post-conversation handlers.
"""

import asyncio
import contextlib
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
from tachikoma.events import StatusCallback
from tachikoma.sdk_query import stderr_aware_query
from tachikoma.sessions.model import Session, SessionContextEntry
from tachikoma.sessions.registry import SessionRegistry

_log = logger.bind(component="post_processing")

WORKSPACE_VALIDATION_SECTION = """\
## Workspace Validation

Before writing a memory that contains claims about workspace state — file paths, \
project structure, configuration values, implementation details — validate each \
claim against the actual workspace:

1. Identify verifiable claims in the memory you're about to write:
   - References to specific files or directories (do they exist? contain what's claimed?)
   - Configuration values (does the config file actually say that?)
   - Implementation details (does the code actually work that way?)
   - Project state (is the project actually in that state?)

2. For verifiable claims, use the Agent tool to spawn validation sub-agents:
   - subagent_type: "Explore"
   - model: "haiku"
   - Batch related claims into a single call where possible
   - The agent should read the relevant file(s) and respond with "VALID" or \
"INVALID: reason" for each claim

3. Only include VALID claims in the written memory:
   - If a claim is INVALID, omit it
   - If ALL claims are invalid, do not create the file

Do NOT validate: subjective information, preferences, general knowledge, \
conversation summaries, personal details — only verifiable claims about \
workspace state."""

# Fixed phase identifiers — validated at registration
MAIN_PHASE = "main"
PRE_FINALIZE_PHASE = "pre_finalize"
FINALIZE_PHASE = "finalize"
_VALID_PHASES = frozenset({MAIN_PHASE, PRE_FINALIZE_PHASE, FINALIZE_PHASE})
_UNSET = object()


class PostProcessor(ABC):
    """Abstract base class for post-processing handlers.

    Subclasses implement process() to perform their specific extraction
    or update logic. The ABC defines only the interface contract — no
    SDK coupling is inherited.
    """

    phase: str = MAIN_PHASE
    _status_message: str = "Processing..."

    def status_message(self) -> str:
        """Short message describing what this processor does."""
        return self._status_message

    @abstractmethod
    async def process(self, session: Session, *, extra: dict | None = None) -> None:
        """Process a closed session.

        Args:
            session: The closed session with sdk_session_id for forking.
            extra: Optional dict carrying additional context for processors.
                Keys are defined by the pipeline; current keys:
                - ``"context_summary"`` (str): Summary of context entries loaded
                  during the session.
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

    async def process(self, session: Session, *, extra: dict | None = None) -> None:
        """Process by forking the SDK session with the configured prompt.

        If the session was resumed from a previous conversation (indicated by
        last_resumed_at), an augmentation is appended to the prompt to provide
        context to the forked agent. If ``extra`` contains a ``context_summary``,
        it is appended to give the forked agent awareness of what context was
        active during the conversation.

        Args:
            session: The closed session to process.
            extra: Optional dict with additional context for processors.
        """
        name = self.__class__.__name__
        _log.info("Processor started: processor={name}", name=name)

        prompt = augment_prompt_for_resumption(self._prompt, session)

        context_summary = (extra or {}).get("context_summary")
        if context_summary is not None:
            prompt = f"{prompt}\n\n{context_summary}"

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


def build_context_summary(entries: list[SessionContextEntry]) -> str | None:
    """Build a context summary from session context entries.

    Groups entries by owner and produces a concise summary (names/paths only,
    not full content) that tells post-processors what was active during the
    conversation. Returns None when there are no entries to summarize.

    Args:
        entries: Session context entries to summarize.

    Returns:
        Formatted summary string, or None if nothing to report.
    """
    if not entries:
        return None

    by_owner: dict[str, list[SessionContextEntry]] = {}
    for e in entries:
        by_owner.setdefault(e.owner, []).append(e)

    lines = [
        "## Session Context",
        "",
        "The following context was active during this conversation:",
        "",
    ]
    has_content = False

    # Foundational: soul, user, agents
    foundational = [o for o in ("soul", "user", "agents") if o in by_owner]
    if foundational:
        has_content = True
        names = [f"{o.upper()}.md" for o in foundational]
        lines.append(f"**Foundational Context:** {', '.join(names)}")

    # Skills: extract enriched metadata (description, path) when available
    skill_entries = by_owner.get("skills", [])
    if skill_entries:
        skills_by_name: dict[str, dict[str, str | None]] = {}
        for e in skill_entries:
            if not e.metadata:
                continue
            name = e.metadata.get("skill_name")
            if not name:
                continue
            skills_by_name.setdefault(
                name,
                {
                    "description": e.metadata.get("skill_description"),
                    "path": e.metadata.get("skill_path"),
                },
            )

        if skills_by_name:
            has_content = True
            skill_lines = []
            for name in sorted(skills_by_name):
                info = skills_by_name[name]
                desc = info["description"]
                path = info["path"]
                if desc and path:
                    skill_lines.append(f"- **{name}** — {desc}: `{path}`")
                else:
                    skill_lines.append(f"- **{name}**")
            lines.append("**Active Skills:**")
            lines.extend(skill_lines)

    # Projects: parse names from content lines like "- name: branch"
    project_entries = by_owner.get("projects", [])
    if project_entries:
        project_names: set[str] = set()
        for entry in project_entries:
            for line in entry.content.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    name_part = stripped[2:].split(":")[0].strip()
                    if name_part:
                        project_names.add(name_part)

        if project_names:
            has_content = True
            lines.append(f"**Projects:** {', '.join(sorted(project_names))}")

    # Previous summary
    if "previous-summary" in by_owner:
        has_content = True
        lines.append("**Previous Conversation:** summary from previous session")

    # Bridging context
    bridging = by_owner.get("bridging-context", [])
    if bridging:
        has_content = True
        count = len(bridging)
        word = "summary" if count == 1 else "summaries"
        lines.append(f"**Bridging Context:** {count} intermediate session {word}")

    if not has_content:
        return None

    lines.append("")
    lines.append(
        "Authority order: Skills (most authoritative) > Memory facts > "
        "Context files (summaries and pointers). When creating or updating "
        "memory files, prefer pointers over duplicating detailed content "
        "that already exists in skills or fact files."
    )
    lines.append(
        "This context was already available to the agent. You should still "
        "search for existing files before creating or updating them (this "
        "list does not replace that search). Use this information to:"
    )
    lines.append(
        "- Skip re-extracting information that is already fully covered by "
        "loaded memory files — but do update those files if the conversation "
        "adds new details or corrections"
    )
    lines.append(
        "- Avoid duplicating content that was provided by active skills into "
        "memory files — when a skill's directory path is listed, read its "
        "SKILL.md before writing entries on the same topic"
    )
    lines.append(
        "- Understand that foundational context (SOUL/USER/AGENTS) shaped "
        "the agent's behavior during the conversation"
    )

    return "\n".join(lines)


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

    def register(self, processor: PostProcessor, phase: str | object = _UNSET) -> None:
        """Register a processor to run on pipeline execution.

        Args:
            processor: The processor to register.
            phase: The phase to run this processor in. Must be "main", "pre_finalize",
                or "finalize". When omitted, reads the processor's ``phase`` class
                attribute (defaults to ``"main"``).

        Raises:
            ValueError: If phase is not a valid phase identifier.
        """
        if phase is _UNSET:
            phase = processor.phase
        assert isinstance(phase, str)
        if phase not in _VALID_PHASES:
            valid_list = ", ".join(sorted(_VALID_PHASES))
            raise ValueError(f"Invalid phase '{phase}'. Valid phases: {valid_list}")
        self._phases[phase].append(processor)

    def unregister(self, processor: PostProcessor) -> None:
        """Unregister a processor from the pipeline.

        Safe no-op if the processor is not in any phase list.
        No lock needed — lifecycle events are serialized by the
        plugin manager's async lock.

        Args:
            processor: The processor to unregister.
        """
        for phase_list in self._phases.values():
            with contextlib.suppress(ValueError):
                phase_list.remove(processor)

    async def run(
        self,
        session: Session,
        *,
        on_status: StatusCallback | None = None,
    ) -> None:
        """Run all registered processors in sequential phases.

        Sets ``is_processing`` before acquiring the lock (for immediate
        caller visibility), runs phases in order (main → pre_finalize →
        finalize) with processors parallel within each phase, then marks
        the session as processed via the registry.

        Individual processor failures are logged per DES-002 but don't
        propagate or prevent subsequent phases from running.

        If *on_status* is provided, it is called with each processor's
        status message before the processor runs.
        """
        self._is_processing = True

        try:
            async with self._lock:
                _log.info("Pipeline started: session={sid}", sid=session.id[:8])

                # Build context summary from session entries
                extra: dict | None = None
                try:
                    entries = await self._registry.load_context_entries(session.id)
                    context_summary = build_context_summary(entries)
                    if context_summary is not None:
                        extra = {"context_summary": context_summary}
                except Exception as exc:
                    _log.exception(
                        "Failed to build context summary (processors will run "
                        "without it): session={sid} err={err}",
                        sid=session.id[:8],
                        err=str(exc),
                    )

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

                    async def _run_one(p: PostProcessor) -> None:
                        if on_status is not None:
                            try:
                                await on_status(p.status_message())
                            except Exception:
                                _log.exception(
                                    "Status callback failed: processor={name}",
                                    name=p.__class__.__name__,
                                )
                        await p.process(session, extra=extra)

                    results = await asyncio.gather(
                        *[_run_one(p) for p in processors],
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


def _split_compound_commands(command: str) -> list[str]:
    """Split a shell command on compound operators, respecting quoting.

    Splits on ``&&``, ``||``, ``|``, and ``;`` only when they appear
    outside single quotes, double quotes, and backslash escapes.
    This avoids false splits on characters like ``|`` inside quoted
    arguments (e.g. ``grep -E "pattern1|pattern2"``).
    """
    parts: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    i = 0

    while i < len(command):
        char = command[i]

        if in_single_quote:
            current.append(char)
            if char == "'":
                in_single_quote = False

        elif in_double_quote:
            if char == "\\":
                current.append(char)
                if i + 1 < len(command):
                    i += 1
                    current.append(command[i])
                i += 1
                continue
            current.append(char)
            if char == '"':
                in_double_quote = False

        else:
            if char == "'":
                current.append(char)
                in_single_quote = True

            elif char == '"':
                current.append(char)
                in_double_quote = True

            elif char == "\\":
                current.append(char)
                if i + 1 < len(command):
                    i += 1
                    current.append(command[i])
                i += 1
                continue

            elif char in ("|", ";") or (
                char == "&" and i + 1 < len(command) and command[i + 1] == "&"
            ):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                # Two-char operators (&&, ||): skip the second character
                if char in ("&", "|") and i + 1 < len(command) and command[i + 1] == char:
                    i += 1

            else:
                current.append(char)

        i += 1

    part = "".join(current).strip()
    if part:
        parts.append(part)

    return parts


def make_bash_gate_hook(allowed_prefixes: list[str]) -> HookMatcher:
    """Create a PreToolUse hook that restricts Bash to specific command prefixes.

    Permission allow rules like ``Bash(git *)`` are not reliably enforced
    by the CLI (known upstream issue). This hook provides programmatic
    enforcement by inspecting the command string before execution.

    Compound commands (joined by ``&&``, ``||``, ``|``, or ``;``) are split
    and each sub-command is checked independently. If any sub-command does
    not match an allowed prefix, the entire command is denied.

    The splitting respects shell quoting — operators inside single quotes,
    double quotes, or after a backslash are treated as literal characters
    and do not trigger a split.

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
        command = (input.get("tool_input") or {}).get("command", "")

        # Split compound commands by shell operators
        parts = _split_compound_commands(command)

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


def make_bash_deny_hook(denied_patterns: list[re.Pattern[str]]) -> HookMatcher:
    """Create a PreToolUse hook that denies Bash commands matching any destructive pattern.

    Inverse polarity of :func:`make_bash_gate_hook` — that hook only allows
    matching commands, this one denies matching commands and passes
    everything else through.

    Compound commands (joined by ``&&``, ``||``, ``|``, or ``;``) are
    split and each sub-command is checked independently against every
    pattern. If *any* sub-command matches *any* pattern, the entire
    command is denied. Otherwise the hook returns an empty decision and
    the command is handled normally by the CLI's permission system.

    The splitting respects shell quoting — operators inside single quotes,
    double quotes, or after a backslash are treated as literal characters
    and do not trigger a split.

    Patterns are matched against each split sub-command via ``pattern.match()``
    (anchored at the start of the sub-command). Compound splitting already
    strips surrounding whitespace per sub-command.

    Args:
        denied_patterns: Compiled regex patterns. A match on any sub-command
            triggers denial.

    Returns:
        A ``HookMatcher`` targeting the Bash tool with one hook callback.
    """

    async def _hook(
        input: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        command = (input.get("tool_input") or {}).get("command", "")

        # Split compound commands by shell operators
        parts = _split_compound_commands(command)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            for pattern in denied_patterns:
                if pattern.match(part):
                    _log.warning(
                        "Bash denied by deny hook: command={cmd} denied_part={part} "
                        "pattern={pattern}",
                        cmd=command[:80],
                        part=part[:80],
                        pattern=pattern.pattern,
                    )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"Sub-command '{part}' matches destructive "
                                f"pattern {pattern.pattern}"
                            ),
                        },
                    }

        # No sub-command matched any deny pattern
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
    "sort ",
    "stat ",
    "cd",
    "pwd",
]

UTILITY_BASH_HOOK = make_bash_gate_hook(UTILITY_BASH_PREFIXES)

# Extended utility prefixes that also allow file deletion (rm).
# Used by maintenance agents that need to prune obsolete files.
MAINTENANCE_BASH_PREFIXES = [*UTILITY_BASH_PREFIXES, "rm "]
MAINTENANCE_BASH_HOOK = make_bash_gate_hook(MAINTENANCE_BASH_PREFIXES)



def build_permissions_settings(allow: list[str]) -> str:
    """Build a settings JSON string with allow-only permission rules.

    Args:
        allow: List of permission allow rules. Use :func:`abs_rule` for
            path-scoped rules to ensure absolute paths.

    Returns:
        JSON string suitable for ``ClaudeAgentOptions.settings``.
    """
    return json.dumps({"permissions": {"allow": allow}})


def _build_fork_options(
    session: Session,
    agent_defaults: AgentDefaults,
    *,
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
) -> tuple[ClaudeAgentOptions, str]:
    """Build the ``ClaudeAgentOptions`` shared by every fork helper.

    When ``tools`` and ``allow`` are both provided, the forked agent runs
    under ``dontAsk`` mode with explicit allow rules; otherwise it runs
    under ``bypassPermissions``.

    Returns the options together with the validated ``sdk_session_id`` so
    callers can emit debug logs without re-narrowing.
    """
    sdk_session_id = session.sdk_session_id
    if sdk_session_id is None:
        raise RuntimeError(f"Cannot fork session {session.id}: no sdk_session_id available")

    options = ClaudeAgentOptions(
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        disallowed_tools=list(agent_defaults.disallowed_tools),
        resume=sdk_session_id,
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

    return options, sdk_session_id


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
    """Fork the SDK session and drain the agent's response stream.

    Creates a forked session via the standalone ``query()`` function,
    independent of the coordinator's ClaudeSDKClient.

    Args:
        session: The session to fork (must have sdk_session_id).
        prompt: The extraction prompt to send to the forked agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).
        mcp_servers: Optional MCP servers to provide to the forked agent.
        system_prompt_append: Optional text appended to the Claude Code
            preset system prompt.
        tools: Optional tool restriction list for the forked agent.
        allow: Optional allow-only permission rules. When paired with
            ``tools``, enables ``dontAsk`` mode instead of
            ``bypassPermissions``.
        pre_tool_use_hooks: Optional PreToolUse hook matchers.
        model: Optional model override; defaults to the parent session's.

    Raises:
        RuntimeError: If session has no sdk_session_id.
        Propagates: SDK errors from the query() call.
    """
    options, sdk_session_id = _build_fork_options(
        session,
        agent_defaults,
        mcp_servers=mcp_servers,
        system_prompt_append=system_prompt_append,
        tools=tools,
        allow=allow,
        pre_tool_use_hooks=pre_tool_use_hooks,
        model=model,
    )

    _log.debug("Forking session: sdk_session_id={sid}", sid=sdk_session_id[:8])

    async for _ in stderr_aware_query(prompt=prompt, options=options):
        pass

    _log.debug("Fork completed: sdk_session_id={sid}", sid=sdk_session_id[:8])


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
    """Fork the SDK session and return the concatenated text response.

    Same shape as :func:`fork_and_consume` but captures and returns the
    text content from every block in the response stream. Returns an
    empty string if no text is produced.

    Raises:
        RuntimeError: If session has no sdk_session_id.
        Propagates: SDK errors from the query() call.
    """
    options, sdk_session_id = _build_fork_options(
        session,
        agent_defaults,
        system_prompt_append=system_prompt_append,
        tools=tools,
        allow=allow,
        pre_tool_use_hooks=pre_tool_use_hooks,
        model=model,
    )

    _log.debug("Forking session for capture: sdk_session_id={sid}", sid=sdk_session_id[:8])

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
        sid=sdk_session_id[:8],
        length=len(result),
    )

    return result


async def query_and_consume(
    prompt: str,
    agent_defaults: AgentDefaults,
    tools: list[str] | None = None,
    allow: list[str] | None = None,
    pre_tool_use_hooks: list[HookMatcher] | None = None,
    model: str | None = None,
) -> None:
    """Spawn a fresh agent and consume its response.

    Creates a fresh query() call with no session forking. Used for
    tasks that don't need conversation context.

    When ``tools`` and ``allow`` are provided, the agent uses
    ``dontAsk`` permission mode with explicit allow rules instead of
    ``bypassPermissions``.

    Args:
        prompt: The prompt to send to the agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).
        tools: Optional tool restriction list for the agent.
        allow: Optional allow-only permission rules for scoping.
        pre_tool_use_hooks: Optional PreToolUse hook matchers.
        model: Optional model alias for the spawned agent. When None
            (the default), the SDK default model is used.

    Raises:
        Propagates: SDK errors from the query() call.
    """
    options = ClaudeAgentOptions(
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        disallowed_tools=list(agent_defaults.disallowed_tools),
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

    _log.debug("Spawning query agent")

    # Fully consume the async iterator to ensure the agent completes
    async for _ in stderr_aware_query(prompt=prompt, options=options):
        pass

    _log.debug("Query agent completed")

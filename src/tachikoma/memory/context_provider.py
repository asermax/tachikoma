"""Memory context provider for per-message pre-processing pipeline.

Uses an Opus agent to search stored memories for context relevant
to the current user message. Runs on every message, using session
forking when conversation context is available to make informed
relevance decisions.

Returns one ContextResult per relevant memory file, with metadata
identifying the file path for deduplication. Episodic memories
are returned as agent-extracted snippets; facts and preferences
are loaded in full.
"""

import re
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.per_message_pre_processing import MessageContextProvider
from tachikoma.pre_processing import ContextResult
from tachikoma.sessions.model import SessionContextEntry

_log = logger.bind(component="memory_context")

MEMORIES_OWNER = "memories"
MEMORY_PATH_META_KEY = "memory_path"
_NO_RELEVANT_MEMORIES = "NO_RELEVANT_MEMORIES"

MEMORY_SEARCH_PROMPT = """\
You are a memory search agent. Your task is to search the \
workspace's stored memories and find information relevant to the user's current message.

## Conversation Context

If you can see previous conversation messages above this prompt, use them \
to evaluate whether the latest message introduces topics not already covered \
by the conversation. If the existing conversation context already covers what's \
needed, respond with NO_RELEVANT_MEMORIES. If no previous messages are visible, \
search based solely on the user's message below.

## Search Strategy

1. Search the following directories for relevant memories:
   - `$WORKSPACE/memories/episodic/` — Date-stamped conversation summaries (can be very large)
   - `$WORKSPACE/memories/facts/` — Factual information (topic-named files)
   - `$WORKSPACE/memories/preferences/` — User preferences (topic-named files)

2. Use this search strategy:
   - First, use Glob to discover files in each $WORKSPACE/memories/ subdirectory
   - Then, use Grep to narrow by keywords/topics from the user's message
   - Finally, use Read to verify relevance of promising candidates

## Scope and Efficiency

- **ONLY search files under `$WORKSPACE/memories/`.** Do not read files outside this \
directory, even if they seem relevant to the conversation. Your job is memory \
retrieval, not general research.
- Be efficient: prefer Grep to narrow candidates before using Read. \
Stop searching once you have enough relevant results or have checked the most \
promising candidates.
- Do NOT attempt to answer the user's message or take any action. \
Return only memory references.

## Output Format

Return each relevant memory as an XML `<memory>` element with a `path` attribute. \
Use absolute paths as shown in the examples below.

For **facts and preferences** files (under `$WORKSPACE/memories/facts/` or \
`$WORKSPACE/memories/preferences/`), return a self-closing tag — the full file \
will be loaded:

    <memory path="$WORKSPACE/memories/facts/restaurants.md" />

For **episodic** files (under `$WORKSPACE/memories/episodic/`), extract ONLY the \
relevant section(s) from the file and include them as the element body. Episodic \
files can be very large, so including only the relevant snippet is critical:

    <memory path="$WORKSPACE/memories/episodic/2026-04-06.md">
    ## Relevant Section Title
    Relevant excerpt from the episodic memory...
    </memory>

Do not include any text outside of `<memory>` elements (except the no-search sentinel).

## No-Search Sentinel

If no relevant memories are found — or if you determine the existing \
conversation context already covers what's needed — respond with exactly: \
`NO_RELEVANT_MEMORIES`

## User's Message

{message}
"""


@dataclass
class ParsedMemoryEntry:
    """A single memory entry parsed from the search agent's output."""

    path: str
    snippet: str | None  # None = load full file; str = use this snippet


_SELF_CLOSING_RE = re.compile(r'<memory\s+path="([^"]+)"\s*/>')
_OPEN_TAG_RE = re.compile(r'<memory\s+path="([^"]+)"\s*>')
_CLOSE_TAG_RE = re.compile(r"</memory>")


def parse_memory_entries(raw_output: str) -> list[ParsedMemoryEntry]:
    """Parse structured memory entries from the search agent's output.

    Handles two forms:
    - Self-closing: ``<memory path="..." />`` -> full file load
    - Open/close: ``<memory path="...">snippet</memory>`` -> snippet extraction

    Malformed entries (unclosed tags) are logged and skipped.

    Returns:
        List of parsed entries. Empty list if none found.
    """
    entries: list[ParsedMemoryEntry] = []

    for match in _SELF_CLOSING_RE.finditer(raw_output):
        entries.append(ParsedMemoryEntry(path=match.group(1), snippet=None))

    # Remove self-closing tags so they don't interfere with open/close parsing
    remaining = _SELF_CLOSING_RE.sub("", raw_output)

    pos = 0

    while pos < len(remaining):
        open_match = _OPEN_TAG_RE.search(remaining, pos)

        if open_match is None:
            break

        close_match = _CLOSE_TAG_RE.search(remaining, open_match.end())

        if close_match is None:
            _log.warning(
                "Unclosed <memory> tag, skipping: path={path}",
                path=open_match.group(1),
            )
            break

        path = open_match.group(1)
        snippet = remaining[open_match.end() : close_match.start()].strip()

        entries.append(
            ParsedMemoryEntry(path=path, snippet=snippet if snippet else None),
        )

        pos = close_match.end()

    return entries


def extract_memory_paths(entries: list[SessionContextEntry]) -> set[str]:
    """Extract loaded memory paths from context entry metadata.

    Reads metadata["memory_path"] from entries where owner="memories" and metadata
    is not None. Gracefully handles entries without metadata.

    Args:
        entries: List of session context entries to inspect.

    Returns:
        Set of memory file paths found in entry metadata.
    """
    paths: set[str] = set()

    for entry in entries:
        if entry.owner != MEMORIES_OWNER or entry.metadata is None:
            continue

        memory_path = entry.metadata.get(MEMORY_PATH_META_KEY)
        if memory_path is not None:
            paths.add(memory_path)

    return paths


class MemoryContextProvider(MessageContextProvider):
    """Context provider that searches stored memories for relevant context.

    Uses an Opus agent with low effort and file search tools to find
    memories relevant to the current user message. Forks the current
    SDK session when available so the search agent has full conversation
    context for informed relevance decisions.

    Returns one ContextResult per relevant memory file, each with
    metadata identifying the file path for deduplication.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the provider.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env, model).
        """
        self._agent_defaults = agent_defaults

    async def provide(
        self,
        message: str,
        *,
        existing_entries: list[SessionContextEntry] | None = None,
        sdk_session_id: str | None = None,
    ) -> list[ContextResult] | None:
        """Search memories for context relevant to the message.

        Args:
            message: The user's message text.
            existing_entries: The session's current context entries.
            sdk_session_id: The current SDK session ID for forking.

        Returns:
            List of ContextResult instances (one per memory file), or None.
        """
        loaded_paths = extract_memory_paths(existing_entries or [])

        workspace = str(self._agent_defaults.cwd)
        prompt = MEMORY_SEARCH_PROMPT.replace("$WORKSPACE", workspace).format(message=message)

        options = ClaudeAgentOptions(
            model=self._agent_defaults.model,
            effort="low",
            max_turns=12,
            tools=["Read", "Glob", "Grep"],
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="bypassPermissions",
            cwd=self._agent_defaults.cwd,
            cli_path=self._agent_defaults.cli_path,
            env=self._agent_defaults.env,
            resume=sdk_session_id if sdk_session_id is not None else None,
            fork_session=sdk_session_id is not None,
        )

        # Fully consume the query() generator per DES-005 — no early
        # return/break inside the async for loop.
        parsed_entries: list[ParsedMemoryEntry] = []

        try:
            async for sdk_message in query(prompt=prompt, options=options):
                if isinstance(sdk_message, ResultMessage):
                    if sdk_message.is_error:
                        _log.warning(
                            "Memory search agent returned error: err={err}",
                            err=sdk_message.result,
                        )
                    elif sdk_message.result is not None:
                        stripped = sdk_message.result.strip()

                        if stripped == _NO_RELEVANT_MEMORIES:
                            _log.debug("No relevant memories found for message")
                        else:
                            parsed_entries = parse_memory_entries(stripped)
                            _log.debug(
                                "Memory search returned entries: count={count}",
                                count=len(parsed_entries),
                            )

        except Exception as exc:
            _log.exception(
                "Memory search agent failed: err={err}",
                err=str(exc),
            )
            return None

        if not parsed_entries:
            return None

        workspace = self._agent_defaults.cwd.resolve()
        memories_root = (workspace / "memories").resolve()

        validated: list[ParsedMemoryEntry] = []

        for entry in parsed_entries:
            resolved = (workspace / entry.path).resolve()

            if not resolved.is_relative_to(memories_root):
                _log.warning(
                    "Memory path outside memories/ rejected: path={path}",
                    path=entry.path,
                )
                continue

            if entry.path in loaded_paths:
                continue

            validated.append(entry)

        if not validated:
            return None

        results: list[ContextResult] = []

        for entry in validated:
            if entry.snippet is not None:
                content = f"[Source: {entry.path}]\n\n{entry.snippet}"
            else:
                file_path = workspace / entry.path

                try:
                    content = file_path.read_text()
                except FileNotFoundError:
                    _log.warning(
                        "Memory file deleted between search and read: path={path}",
                        path=entry.path,
                    )
                    continue

            results.append(
                ContextResult(
                    tag=MEMORIES_OWNER,
                    content=content,
                    metadata={MEMORY_PATH_META_KEY: entry.path},
                )
            )

        return results if results else None

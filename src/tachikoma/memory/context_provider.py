"""Memory context provider for per-message pre-processing pipeline.

Uses an Opus agent to search stored memories for context relevant
to the current user message. Runs on every message, receiving the
session summary and last exchange for informed relevance decisions.

Returns one ContextResult per relevant memory file, with metadata
identifying the file path for deduplication. Episodic memories
are returned as agent-extracted snippets; facts and preferences
are loaded in full.
"""

import re
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import ResultMessage
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.message import IncomingMessage
from tachikoma.per_message_pre_processing import (
    MessageContextProvider,
    render_conversation_context,
)
from tachikoma.post_processing import abs_rule, build_permissions_settings
from tachikoma.pre_processing import ContextResult
from tachikoma.sdk_query import stderr_aware_query
from tachikoma.sessions.model import SessionContextEntry

_log = logger.bind(component="memory_context")

MEMORIES_OWNER = "memories"
MEMORY_PATH_META_KEY = "memory_path"
_NO_RELEVANT_MEMORIES = "NO_RELEVANT_MEMORIES"

MEMORY_SEARCH_PROMPT = """\
You are a memory search agent. Your task is to search the \
workspace's stored memories and find information relevant to the user's current message.
{conversation_context_section}
## Classification

Before searching, classify the user's message into one of these tiers:

1. **Skip** — Purely social/transactional with no informational content:
   - Greetings: "hi", "hello", "hey", "good morning"
   - Acknowledgments: "ok", "thanks", "got it", "sure", "sounds good", "great"
   - Short yes/no: "yes", "no", "maybe", "right"
   → Return `NO_RELEVANT_MEMORIES` immediately — do not search any files.

2. **Shallow** — Continuation of the current topic (only when conversation \
context is present and the message clearly extends the ongoing discussion):
   - Uses pronouns from the discussion: "what about that?", "and the other one?"
   - Follow-up within same domain: "what else?", "tell me more"
   → Grep `$WORKSPACE/memories/facts/` and `$WORKSPACE/memories/preferences/` \
for terms from the message. Skip episodic search entirely.

3. **Full** — Any of the following:
   - Introduces a new topic
   - References past context: "what did we discuss about X?", "remember when", \
"last time"
   - Contains a question or request (even with a greeting): "hi, remind me about..."
   - Unclear classification
   → Full search across all memory directories.

**Rule**: When in doubt, default to Full search. It is better to search \
unnecessarily (across all directories) than to miss relevant context.

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

## Permissions

You can only read and search files within `$WORKSPACE/memories/`. Access outside \
this directory will be denied.

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
    memories relevant to the current user message. Receives the session
    summary and last exchange for informed relevance decisions.

    Returns one ContextResult per relevant memory file, each with
    metadata identifying the file path for deduplication.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the provider.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env, model).
        """
        self._agent_defaults = agent_defaults

    def status_message(self, result: list[ContextResult] | None = None) -> str:
        if result is None:
            return "Searching memories..."

        count = len(result) if result else 0

        if count:
            return f"Found {count} relevant memories"

        return "No relevant memories found"

    async def provide(
        self,
        message: IncomingMessage,
        *,
        existing_entries: list[SessionContextEntry] | None = None,
        sdk_session_id: str | None = None,
        session_summary: str | None = None,
        session_last_exchange: str | None = None,
    ) -> list[ContextResult] | None:
        """Search memories for context relevant to the message.

        Args:
            message: The incoming message envelope.
            existing_entries: The session's current context entries.
            sdk_session_id: The current SDK session ID (unused, kept for ABC compat).
            session_summary: The active session's rolling summary, if available.
            session_last_exchange: The active session's last assistant response, if available.

        Returns:
            List of ContextResult instances (one per memory file), or None.
        """
        loaded_paths = extract_memory_paths(existing_entries or [])

        workspace = str(self._agent_defaults.cwd)
        conversation_context_section = render_conversation_context(
            session_summary, session_last_exchange
        )
        prompt = MEMORY_SEARCH_PROMPT.replace("$WORKSPACE", workspace).format(
            message=message.text, conversation_context_section=conversation_context_section
        )

        options = ClaudeAgentOptions(
            model=self._agent_defaults.searcher_model,
            effort="low",
            tools=["Read", "Glob", "Grep"],
            allowed_tools=["Read", "Glob", "Grep"],
            settings=build_permissions_settings(
                [
                    abs_rule("Read", self._agent_defaults.cwd / "memories"),
                    "Glob",
                    "Grep",
                ]
            ),
            extra_args={"permission-mode": "dontAsk"},
            cwd=self._agent_defaults.cwd,
            cli_path=self._agent_defaults.cli_path,
            env=self._agent_defaults.env,
        )

        # Fully consume the query() generator per DES-005 — no early
        # return/break inside the async for loop.
        parsed_entries: list[ParsedMemoryEntry] = []

        try:
            async for sdk_message in stderr_aware_query(prompt=prompt, options=options):
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

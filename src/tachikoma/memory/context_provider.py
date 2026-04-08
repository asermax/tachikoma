"""Memory context provider for per-message pre-processing pipeline.

Uses an Opus agent to search stored memories for context relevant
to the current user message. Runs on every message, using session
forking when conversation context is available to make informed
relevance decisions.

Returns one ContextResult per relevant memory file, with metadata
identifying the file path for deduplication.
"""

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
   - `memories/episodic/` — Date-stamped conversation summaries
   - `memories/facts/` — Factual information (topic-named files)
   - `memories/preferences/` — User preferences (topic-named files)

2. Use this search strategy:
   - First, use Glob to discover files in each memories/ subdirectory
   - Then, use Grep to narrow by keywords/topics from the user's message
   - Finally, use Read to verify relevance of promising candidates

## Output Format

Return ONLY the file paths of relevant memories, one per line.
Do not include descriptions, summaries, or other text.
Paths must be relative to the workspace root (e.g., memories/facts/restaurants.md).

## No-Search Sentinel

If no relevant memories are found — or if you determine the existing \
conversation context already covers what's needed — respond with exactly: \
`NO_RELEVANT_MEMORIES`

## User's Message

{message}
"""


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

        prompt = MEMORY_SEARCH_PROMPT.format(message=message)

        options = ClaudeAgentOptions(
            model=self._agent_defaults.model,
            effort="low",
            max_turns=8,
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
        raw_paths: list[str] = []

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
                            raw_paths = [
                                p.strip() for p in stripped.split("\n") if p.strip()
                            ]
                            _log.debug(
                                "Memory search returned paths: count={count}",
                                count=len(raw_paths),
                            )

        except Exception as exc:
            _log.exception(
                "Memory search agent failed: err={err}",
                err=str(exc),
            )
            return None

        if not raw_paths:
            return None

        workspace = self._agent_defaults.cwd.resolve()
        memories_root = (workspace / "memories").resolve()

        validated_paths: list[str] = []

        for raw_path in raw_paths:
            resolved = (workspace / raw_path).resolve()

            if not resolved.is_relative_to(memories_root):
                _log.warning(
                    "Memory path outside memories/ rejected: path={path}",
                    path=raw_path,
                )
                continue

            if raw_path in loaded_paths:
                continue

            validated_paths.append(raw_path)

        if not validated_paths:
            return None

        results: list[ContextResult] = []

        for path in validated_paths:
            file_path = workspace / path

            try:
                content = file_path.read_text()
            except FileNotFoundError:
                _log.warning(
                    "Memory file deleted between search and read: path={path}",
                    path=path,
                )
                continue

            results.append(
                ContextResult(
                    tag=MEMORIES_OWNER,
                    content=content,
                    metadata={MEMORY_PATH_META_KEY: path},
                )
            )

        return results if results else None

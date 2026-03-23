"""Memory context provider for pre-processing pipeline.

Uses an Opus agent to search stored memories for context relevant
to the current user message.
"""

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.pre_processing import ContextProvider, ContextResult

_log = logger.bind(component="memory_context")

MEMORY_SEARCH_PROMPT = """You are a memory search agent. Your task is to search the \
workspace's stored memories and find information relevant to the user's current message.

## Instructions

1. Search the following directories for relevant memories:
   - `memories/episodic/` — Date-stamped conversation summaries
   - `memories/facts/` — Factual information (topic-named files)
   - `memories/preferences/` — User preferences (topic-named files)

2. Use this search strategy:
   - First, use Glob to discover files in each memories/ subdirectory
   - Then, use Grep to narrow by keywords/topics from the user's message
   - Finally, use Read to verify relevance of promising candidates

3. Return a ranked list of the most relevant memory files (up to 10):
   - Each entry should include the file path and a short summary
   - Rank by relevance to the user's current message
   - Focus on files that would help the main agent respond better

4. If you find relevant memories, format your response as:
   ```markdown
   ## Relevant Memories

   1. **memories/episodic/2026-03-13.md** — Brief summary of what's in this file
   2. **memories/facts/restaurants.md** — Information about restaurants the user likes
   ...

   **Instructions for the main agent**: Read the files above if you need more detail \
about any of these memories.
   ```

5. If no relevant memories are found (including when the directories are empty), \
respond with exactly: `NO_RELEVANT_MEMORIES`

## User's Message

{message}

---

Search the memories and return relevant files, or respond with NO_RELEVANT_MEMORIES \
if nothing relevant is found.
"""


class MemoryContextProvider(ContextProvider):
    """Context provider that searches stored memories for relevant context.

    Uses an Opus agent with low effort and file search tools to find
    memories relevant to the current user message. Returns a ContextResult
    with the "memories" tag containing a ranked list of relevant files.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the provider.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        self._agent_defaults = agent_defaults

    async def provide(self, message: str) -> ContextResult | None:
        """Search memories for context relevant to the message.

        Args:
            message: The user's message text.

        Returns:
            ContextResult with relevant memories, or None if no relevant
            memories found or an error occurred.
        """
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
        )

        # Fully consume the query() generator to ensure proper SDK cleanup.
        result: ContextResult | None = None

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

                        if stripped == "NO_RELEVANT_MEMORIES":
                            _log.debug("No relevant memories found for message")
                        else:
                            result = ContextResult(tag="memories", content=sdk_message.result)

        except Exception as exc:
            _log.exception(
                "Memory search agent failed: err={err}",
                err=str(exc),
            )

        return result

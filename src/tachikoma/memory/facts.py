"""Facts memory processor.

Extracts factual information about the user from conversations.
"""

from pathlib import Path

from tachikoma.post_processing import PostProcessor, fork_and_consume
from tachikoma.sessions.model import Session

FACTS_PROMPT = """You are a memory extraction agent. Your task is to analyze
the conversation and extract or update factual information about the user.

## Instructions

1. First, read the existing files in the `memories/facts/` directory to see
   what facts are already stored.

2. Analyze the conversation for factual information the user has shared:
   - Personal details (name, location, occupation)
   - Work or project information
   - Relationships, family, or contacts
   - Possessions, tools, or resources
   - Schedule or routine information
   - Any other objective facts about the user's life

3. Manage the fact files:
   - Create new files with descriptive names (e.g., `work-info.md`,
     `location.md`, `family.md`)
   - Update existing files when new information contradicts or extends
     what's there
   - Delete files that are no longer accurate or relevant

4. Each fact file should contain:
   - A clear, factual statement about the user
   - Relevant context or details
   - When appropriate, the date the information was learned

5. **Important constraints**:
   - Only create or modify files within `memories/facts/`
   - Use descriptive, topic-based filenames (not dates)
   - If no new factual information emerged from the conversation,
     it is perfectly acceptable to create no files
   - Do not infer facts that weren't explicitly stated — only record
     what the user actually shared

Remember: These memories help the assistant understand the user's context
and provide personalized assistance. Focus on accurate, verified information."""


class FactsProcessor(PostProcessor):
    """Post-processor for extracting factual memories.

    Creates or updates topic-named files in memories/facts/.
    """

    def __init__(self, cwd: Path) -> None:
        """Initialize the processor.

        Args:
            cwd: The workspace directory for the forked agent.
        """
        self._cwd = cwd

    async def process(self, session: Session) -> None:
        """Extract factual memories from the session.

        Args:
            session: The closed session to process.
        """
        await fork_and_consume(session, FACTS_PROMPT, self._cwd)

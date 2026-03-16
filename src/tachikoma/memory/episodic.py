"""Episodic memory processor.

Extracts date-stamped summaries of conversations from completed sessions.
"""

from pathlib import Path

from tachikoma.post_processing import PromptDrivenProcessor

EPISODIC_PROMPT = """You are a memory extraction agent. Your task is to analyze
the conversation that just ended and create or update episodic memory files.

## Instructions

1. First, read the existing files in the `memories/episodic/` directory to see
   what summaries already exist.

2. Analyze the conversation for meaningful events, discussions, and activities.

3. Create or update date-stamped files using the format `YYYY-MM-DD.md`:
   - If a file for today's date already exists, read it and **consolidate**
     the new information with the existing content rather than creating
     a duplicate file
   - Write a concise summary of what happened during this conversation
   - Include key topics discussed, decisions made, and actions taken

4. Each memory file should contain:
   - A brief summary of the conversation(s) for that day
   - Key points or takeaways
   - Any important context for future reference

5. **Important constraints**:
   - Only create or modify files within `memories/episodic/`
   - If the conversation was trivial or contained no meaningful information,
     it is perfectly acceptable to create no files
   - Do not create duplicate files for the same date — consolidate entries

Remember: These memories help the assistant maintain context across sessions.
Focus on what would be useful to remember about this conversation in the future."""


class EpisodicProcessor(PromptDrivenProcessor):
    """Post-processor for extracting episodic memories.

    Creates or updates date-stamped summary files in memories/episodic/.
    """

    def __init__(self, cwd: Path, cli_path: str | None = None) -> None:
        """Initialize the processor.

        Args:
            cwd: The workspace directory for the forked agent.
            cli_path: Optional path to the Claude CLI binary.
        """
        super().__init__(EPISODIC_PROMPT, cwd, cli_path=cli_path)

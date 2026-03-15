"""CoreContextProcessor for updating foundational context files.

Post-processor that analyzes completed conversations and updates SOUL.md,
USER.md, and AGENTS.md based on conversation learnings. Uses the pending
signals mechanism for ambiguous signals that need recurrence detection.

See DLT-018: Update core context files from conversation learnings.
"""

from pathlib import Path

from loguru import logger

from tachikoma.context.loading import CONTEXT_DIR_NAME, CONTEXT_FILES
from tachikoma.context.tools import (
    clean_pending_signals,
    create_pending_signals_server,
)
from tachikoma.post_processing import PromptDrivenProcessor, fork_and_consume
from tachikoma.sessions.model import Session

_log = logger.bind(component="core_context_processor")

CONTEXT_UPDATE_PROMPT = """\
You are a context file update agent. Your task is to analyze the completed \
conversation and update the foundational context files when appropriate.

## Your Task

1. **Read all three context files:**
   - `context/SOUL.md` — Personality traits, tone, and behavioral guidelines
   - `context/USER.md` — What the assistant knows about the user
   - `context/AGENTS.md` — Operational instructions and workflow preferences

2. **Read pending signals:**
   - Use the `read_pending_signals` tool to check for previously staged signals

3. **Analyze the conversation** for information that should update these files:
   - User information changes (new job, location, projects) → USER.md
   - Personality/behavioral feedback ("be more concise") → SOUL.md
   - Operational instruction changes ("always use pytest") → AGENTS.md

4. **Classify each signal** and take action:

   **Clear & explicit signals** (strong evidence, unambiguous):
   - Update the appropriate context file directly
   - Read the file first, preserve structure, merge changes contextually
   - Replace outdated information when there's clear evidence of change

   **Ambiguous / one-off signals** (single mention, no clear directive):
   - Check pending signals for semantic recurrence (is this similar to past signals?)
   - If recurring pattern detected → promote to context file update
   - If first occurrence → stage via `add_pending_signal` tool for future tracking

   **No relevant information** → do nothing (this is perfectly acceptable)

5. **Important constraints:**
   - **Be conservative**: Only apply changes with clear conversational evidence
   - **Route correctly**: personality→SOUL, user info→USER, instructions→AGENTS
   - **Read-first**: Always read a file before modifying it
   - **Preserve structure**: Keep existing formatting and organization
   - **Tool-only for pending signals**: Only interact with pending signals through \
the provided `read_pending_signals` and `add_pending_signal` tools — never access \
the file directly

## Examples

### Clear Signal → Direct Update
User: "I just started a new job at Acme Corp"
Action: Update USER.md with new employer information

### Ambiguous Signal → Stage
User: "that was too verbose"
Action: Check pending signals. If no similar signal, use `add_pending_signal` to \
stage for recurrence detection.

### Recurring Signal → Promote
Previous signal in pending: "User seemed to prefer shorter responses"
Current message: "your answers are way too long"
Action: This confirms a pattern → update SOUL.md with preference for concise responses

## Remember

These files shape the assistant's identity and behavior across all sessions. \
Updates should be deliberate and evidence-based. When in doubt, stage the signal \
for future recurrence detection rather than making premature changes."""


class CoreContextProcessor(PromptDrivenProcessor):
    """Post-processor for updating foundational context files.

    Analyzes completed conversations and updates SOUL.md, USER.md, and AGENTS.md
    based on clear, explicit signals. Ambiguous signals are staged in the pending
    signals file for recurrence detection.

    Extends PromptDrivenProcessor but overrides process() for:
    - Pre-step: auto-cleanup of expired pending signals
    - MCP tools: pending signals read/add tools for the forked agent
    - Post-step: mtime comparison for observability logging
    """

    def __init__(self, cwd: Path) -> None:
        """Initialize the processor.

        Args:
            cwd: The workspace directory containing the context/ folder.
        """
        super().__init__(CONTEXT_UPDATE_PROMPT, cwd)
        self._data_dir = cwd / ".tachikoma"

    async def process(self, session: Session) -> None:
        """Process the session and update context files.

        This override adds pre/post steps around the fork:
        1. Pre-step: Clean expired pending signals
        2. Snapshot context file mtimes
        3. Fork with MCP tools for pending signals access
        4. Post-step: Log which files changed (if any)

        Args:
            session: The closed session to process.
        """
        # Pre-step: Clean expired pending signals
        clean_pending_signals(self._data_dir)

        # Create MCP server with pending signals tools
        pending_signals_server = create_pending_signals_server(self._data_dir)

        # Snapshot context file mtimes before fork
        context_path = self._cwd / CONTEXT_DIR_NAME
        mtimes_before: dict[str, float | None] = {}
        for filename, _, _ in CONTEXT_FILES:
            file_path = context_path / filename
            try:
                mtimes_before[filename] = file_path.stat().st_mtime
            except FileNotFoundError:
                mtimes_before[filename] = None
            except OSError:
                # File exists but can't stat — treat as unchanged
                mtimes_before[filename] = None

        # Fork session with pending signals tools
        await fork_and_consume(
            session,
            self._prompt,
            self._cwd,
            mcp_servers={"pending-signals": pending_signals_server},
        )

        # Post-step: Compare mtimes and log changes
        for filename, _, _ in CONTEXT_FILES:
            file_path = context_path / filename
            try:
                mtime_after = file_path.stat().st_mtime
            except FileNotFoundError:
                mtime_after = None
            except OSError:
                mtime_after = None

            mtime_before = mtimes_before.get(filename)
            if mtime_before is None and mtime_after is not None:
                _log.info("Context file created: file={file}", file=filename)
            elif mtime_before is not None and mtime_after is None:
                _log.info("Context file deleted: file={file}", file=filename)
            elif (
                mtime_before is not None
                and mtime_after is not None
                and mtime_after != mtime_before
            ):
                _log.info("Context file updated: file={file}", file=filename)

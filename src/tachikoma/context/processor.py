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
    PENDING_SIGNALS_FILENAME,
    clean_pending_signals,
    create_pending_signals_server,
    parse_pending_signals,
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

2. **Review pending signals:**

{pending_signals_section}

   Note: Signal indices (S1, S2, etc.) are stable for this session. Use the \
original numbers even after removals — the indices refer to the positions shown above.

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
   - Check the pending signals list above for semantic recurrence
   - If recurring pattern detected → promote to context file update AND remove \
the promoted signal via `remove_pending_signal`
   - If first occurrence → stage via `add_pending_signal` tool for future tracking

   **Stale or irrelevant signals in the list:**
   - Clean them up via `remove_pending_signal` to prevent noise in future sessions

   **No relevant information** → do nothing (this is perfectly acceptable)

5. **Important constraints:**
   - **Be conservative**: Only apply changes with clear conversational evidence
   - **Route correctly**: personality→SOUL, user info→USER, instructions→AGENTS
   - **Read-first**: Always read a file before modifying it
   - **Preserve structure**: Keep existing formatting and organization
   - **Tool-only for pending signals**: Only interact with pending signals through \
the provided `add_pending_signal` and `remove_pending_signal` tools — never access \
the file directly
   - **Order matters**: Perform all removals before staging new signals to avoid \
overwriting freshly-added entries

## Pending Signals Lifecycle

The pending signals mechanism tracks ambiguous observations that might become \
patterns if they recur:

1. **Stage**: When you notice a potential signal but it's ambiguous or one-off, \
use `add_pending_signal` to record it with today's date.

2. **Promote**: When you detect a recurring pattern in pending signals, update \
the appropriate context file AND use `remove_pending_signal` to clean up the \
promoted entries.

3. **Cleanup**: When you notice stale or irrelevant signals in the list, use \
`remove_pending_signal` to remove them proactively rather than waiting for \
30-day expiry.

## Examples

### Clear Signal → Direct Update
User: "I just started a new job at Acme Corp"
Action: Update USER.md with new employer information

### Ambiguous Signal → Stage
User: "that was too verbose"
Action: Check pending signals above. If no similar signal, use `add_pending_signal` \
to stage for recurrence detection.

### Recurring Signal → Promote and Remove
Pending signals: S1: "User seemed to prefer shorter responses"
Current message: "your answers are way too long"
Action: This confirms a pattern → update SOUL.md with preference for concise \
responses, then call `remove_pending_signal` with indices [1] to clean up S1.

### Stale Signal → Cleanup
Pending signals: S2: "User mentioned liking dark themes" (from 3 weeks ago, \
no recurrence in subsequent conversations)
Action: Call `remove_pending_signal` with indices [2] to clean up the stale signal.

## Remember

These files shape the assistant's identity and behavior across all sessions. \
Updates should be deliberate and evidence-based. When in doubt, stage the signal \
for future recurrence detection rather than making premature changes."""


def _read_pending_signals_snapshot(data_dir: Path) -> list[tuple[str, str]]:
    """Read and parse pending signals file into a snapshot.

    The snapshot is a list of (date_str, signal_text) tuples that represents
    the state of pending signals at the start of the forked session. This
    snapshot is immutable and used for index-based removal.

    Args:
        data_dir: Path to the .tachikoma directory.

    Returns:
        List of (date_str, signal_text) tuples. Empty list if file missing/empty.
    """
    file_path = data_dir / PENDING_SIGNALS_FILENAME

    if not file_path.exists():
        return []

    try:
        content = file_path.read_text()
    except OSError:
        return []

    if not content.strip():
        return []

    return parse_pending_signals(content)


def _format_pending_signals_section(snapshot: list[tuple[str, str]]) -> str:
    """Format the pending signals snapshot for injection into the prompt.

    Creates a numbered list (S1, S2, ...) that the forked agent can reference
    when calling remove_pending_signal.

    Args:
        snapshot: List of (date_str, signal_text) tuples from the snapshot.

    Returns:
        Formatted string for the {pending_signals_section} placeholder.
    """
    if not snapshot:
        return "No pending signals at this time."

    lines = []
    for i, (date_str, signal_text) in enumerate(snapshot, start=1):
        lines.append(f"S{i}: **{date_str}**: {signal_text}")

    return "\n".join(lines)


class CoreContextProcessor(PromptDrivenProcessor):
    """Post-processor for updating foundational context files.

    Analyzes completed conversations and updates SOUL.md, USER.md, and AGENTS.md
    based on clear, explicit signals. Ambiguous signals are staged in the pending
    signals file for recurrence detection.

    Extends PromptDrivenProcessor but overrides process() for:
    - Pre-step: auto-cleanup of expired pending signals
    - Auto-inject pending signals into prompt
    - MCP tools: add_pending_signal and remove_pending_signal for the forked agent
    - Post-step: mtime comparison for observability logging
    """

    def __init__(self, cwd: Path, cli_path: str | None = None) -> None:
        """Initialize the processor.

        Args:
            cwd: The workspace directory containing the context/ folder.
            cli_path: Optional path to the Claude CLI binary.
        """
        super().__init__(CONTEXT_UPDATE_PROMPT, cwd, cli_path=cli_path)
        self._data_dir = cwd / ".tachikoma"

    async def process(self, session: Session) -> None:
        """Process the session and update context files.

        This override adds pre/post steps around the fork:
        1. Pre-step: Clean expired pending signals
        2. Read pending signals snapshot and format prompt
        3. Snapshot context file mtimes
        4. Fork with MCP tools (add_pending_signal, remove_pending_signal)
        5. Post-step: Log which files changed (if any)

        Args:
            session: The closed session to process.
        """
        # Pre-step: Clean expired pending signals
        clean_pending_signals(self._data_dir)

        # Read snapshot and format prompt with pending signals section
        snapshot = _read_pending_signals_snapshot(self._data_dir)
        signals_section = _format_pending_signals_section(snapshot)
        formatted_prompt = self._prompt.replace("{pending_signals_section}", signals_section)

        # Create MCP server with pending signals tools (passing snapshot for remove tool)
        pending_signals_server = create_pending_signals_server(self._data_dir, snapshot)

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
            formatted_prompt,
            self._cwd,
            mcp_servers={"pending-signals": pending_signals_server},
            cli_path=self._cli_path,
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

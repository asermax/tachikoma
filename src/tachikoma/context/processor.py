"""CoreContextProcessor for updating foundational context files.

Post-processor that analyzes completed conversations and updates SOUL.md,
USER.md, and AGENTS.md based on conversation learnings. Uses the pending
signals mechanism for ambiguous signals that need recurrence detection.

See DLT-018: Update core context files from conversation learnings.
"""

from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.context.loading import CONTEXT_DIR_NAME, CONTEXT_FILES
from tachikoma.context.tools import (
    PENDING_SIGNALS_FILENAME,
    clean_pending_signals,
    create_pending_signals_server,
    parse_pending_signals,
)
from tachikoma.post_processing import (
    UTILITY_BASH_HOOK,
    PromptDrivenProcessor,
    abs_rule,
    augment_prompt_for_resumption,
    fork_and_consume,
)
from tachikoma.sessions.model import Session

_log = logger.bind(component="core_context_processor")

CONTEXT_UPDATE_PROMPT = """\
You are a context file update agent. Your task is to analyze the completed \
conversation and update the foundational context files when appropriate.

## Your Task

1. **Read all three context files:**
   - `$WORKSPACE/context/SOUL.md` — Personality traits, tone, and behavioral guidelines
   - `$WORKSPACE/context/USER.md` — What the assistant knows about the user
   - `$WORKSPACE/context/AGENTS.md` — Operational instructions and workflow preferences

2. **Review pending signals:**

{pending_signals_section}

   Note: Signal indices (S1, S2, etc.) are stable for this session. Use the \
original numbers even after removals — the indices refer to the positions shown above.

3. **Analyze the conversation** for information that should update these files:

   **USER.md** — Stable identity and interests. Things that stay true for \
weeks or months:
   - Name, location, employer, profession
   - Broad interests and hobbies ("learning trumpet", "game development")
   - Active project NAMES with one-line descriptions — not status, specs, or progress
   - Communication preferences, learning style
   DO NOT put in USER.md: project status updates, detailed specs, meeting prep, \
daily routine logs, game mechanics, implementation details. If a section is being \
rewritten more than once a week, it's too detailed for USER.md — that content \
belongs in memory files (facts or preferences).

   **SOUL.md** — Personality and behavioral guidelines:
   - Tone and communication style feedback ("be more concise", "push back more")
   - Behavioral instructions that shape the assistant's character

   **AGENTS.md** — Operational instructions and workflow preferences:
   - Tool usage patterns, CLI preferences
   - Workflow conventions, formatting rules
   - System-specific instructions (task scheduling, note creation patterns)

   **Correction Detection** — Watch for moments where the agent was corrected \
and extract the lesson as a behavioral instruction:
   - **Explicit user corrections**: The user directly says "no", "don't", "wrong", \
"actually", or otherwise rejects the agent's approach and provides the right one
   - **Implicit user corrections**: The user restates or rephrases their request \
after the agent gave a clearly wrong answer, or provides the correct answer \
themselves after the agent was wrong — only when the agent demonstrably erred, \
not normal conversational refinement
   - **Agent self-corrections**: The agent acknowledges a mistake ("I was wrong", \
"let me fix that") and provides the corrected approach

   When a correction is detected:
   - Extract the pattern as a concise entry: `- Don't [specific mistake]. \
Do [correct behavior] instead.`
   - Place the entry under the AGENTS.md section that matches its domain \
(e.g., GitHub-related corrections under the GitHub section, skill-related \
corrections under the relevant skill section). This keeps related instructions \
together. If no matching section exists, create one with a descriptive heading.
   - Before adding, read existing entries in that section and skip if a \
semantically similar entry already covers it — or refine the existing entry \
if the correction adds new nuance (e.g., a missing condition or clarified boundary)
   - Keep entries to one line each. No explanations, no context, no history

   **Routing note**: Corrections about task execution, tool usage, or \
problem-solving go to AGENTS.md under the domain-appropriate section. \
Corrections about communication style or tone (e.g., "don't be so formal") \
go to SOUL.md as personality adjustments — those are not corrections.

   Examples:
   - Under a GitHub section: `- Don't use force push on shared branches. \
Use rebase and push normally.`
   - Under a skills section: `- Don't run the full planning workflow for \
single-file changes. Use the patch workflow instead.`
   - Under a general section: `- Don't create new branches for every bug \
fix. Use the patch workflow instead.`

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

5. **Prune stale content** from context files:
   - While reading context files, actively look for content that is outdated \
or no longer accurate:
     - USER.md: projects that were completed or abandoned (confirmed by \
conversation), outdated employer or role info, interests the user has moved \
away from
     - AGENTS.md: instructions about tools or workflows no longer in use, \
outdated conventions
     - SOUL.md: personality adjustments that the user has contradicted or \
reversed
   - Remove or update stale sections to keep files current and concise. Do \
not leave outdated info "just in case" — these files should be a current \
snapshot, not an archive.
   - **Do NOT prune based on**: vague hints, assumptions, or the age of \
content alone (age is not staleness — only prune when you have clear evidence)

6. **Important constraints:**
   - **Be conservative**: Only apply changes with clear conversational evidence
   - **Route correctly**: personality→SOUL, user info→USER, instructions→AGENTS
   - **Read-first**: Always read a file before modifying it
   - **Preserve structure**: Keep existing formatting and organization
   - **Tool-only for pending signals**: Only interact with pending signals through \
the provided `add_pending_signal` and `remove_pending_signal` tools — never access \
the file directly
   - **Order matters**: Perform all removals before staging new signals to avoid \
overwriting freshly-added entries
   - **Watch file size**: USER.md should stay under ~120 lines. If it's growing \
past that, you're including too much detail — summarize, remove stale sections, \
or omit details that belong in facts/preferences memory instead.
   - **Replace, don't append**: When updating a section, rewrite it cleanly \
rather than appending new paragraphs. Each section should read as a current \
snapshot, not a changelog.

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
for future recurrence detection rather than making premature changes.

## Permissions

You can only access files within `context/`. Reads, edits, and writes outside \
this directory will be denied. For Bash, read-only inspection commands (`ls`, \
`find`, `file`, `echo`, `date`, `cat`, `head`, `tail`, `wc`, `stat`) and \
navigation (`cd`, `pwd`) are allowed — other commands will be denied."""


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

    _status_message = "Refreshing core context..."

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        scope = agent_defaults.cwd / CONTEXT_DIR_NAME

        super().__init__(
            CONTEXT_UPDATE_PROMPT,
            agent_defaults,
            tools=["Read", "Glob", "Grep", "Bash", "Edit", "Write"],
            allow=[
                abs_rule("Read", scope),
                "Glob",
                "Grep",
                "Bash",
                abs_rule("Edit", scope),
                abs_rule("Write", scope),
                "mcp__pending-signals__add_pending_signal",
                "mcp__pending-signals__remove_pending_signal",
            ],
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
            model=agent_defaults.processor_model,
        )
        self._data_dir = agent_defaults.cwd / ".tachikoma"

    async def process(self, session: Session, *, extra: dict | None = None) -> None:
        """Process the session and update context files.

        This override adds pre/post steps around the fork:
        1. Pre-step: Clean expired pending signals
        2. Read pending signals snapshot and format prompt
        3. Snapshot context file mtimes
        4. Fork with MCP tools (add_pending_signal, remove_pending_signal)
        5. Post-step: Log which files changed (if any)

        Args:
            session: The closed session to process.
            extra: Optional dict with additional context for processors.
        """
        _log.info("Processor started: processor=CoreContextProcessor")

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

        prompt = augment_prompt_for_resumption(formatted_prompt, session)

        context_summary = (extra or {}).get("context_summary")
        if context_summary is not None:
            prompt = f"{prompt}\n\n{context_summary}"

        # Fork session with pending signals tools
        await fork_and_consume(
            session,
            prompt,
            self._agent_defaults,
            mcp_servers={"pending-signals": pending_signals_server},
            tools=self._tools,
            allow=self._allow,
            pre_tool_use_hooks=self._pre_tool_use_hooks,
            model=self._model,
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
                mtime_before is not None and mtime_after is not None and mtime_after != mtime_before
            ):
                _log.info("Context file updated: file={file}", file=filename)

        _log.info("Processor completed: processor=CoreContextProcessor")

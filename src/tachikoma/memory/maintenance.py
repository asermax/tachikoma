"""Memory maintenance tick functions for scheduled memory store cleanup.

Three tick functions (episodic, facts, preferences) each run an agent-driven
maintenance pass via query_and_consume, then commit changes via git.
"""

from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.config import MaintenanceSettings
from tachikoma.git.processor import GIT_ALLOW, GIT_BASH_HOOK, GIT_TOOLS
from tachikoma.git.sync import has_uncommitted_changes
from tachikoma.post_processing import (
    MAINTENANCE_BASH_HOOK,
    abs_rule,
    query_and_consume,
)

_log = logger.bind(component="memory.maintenance")

MAINTENANCE_TOOLS = ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]


def maintenance_allow_rules(scope: Path) -> list[str]:
    """Build scoped allow rules for a maintenance agent.

    Read/Glob/Grep/Bash are unrestricted. Edit/Write are scoped to the
    given directory.
    """
    return [
        "Read",
        "Glob",
        "Grep",
        "Bash",
        abs_rule("Edit", scope),
        abs_rule("Write", scope),
    ]


async def git_commit_memory_changes(
    agent_defaults: AgentDefaults,
    memory_type: str,
) -> None:
    """Stage and commit changes in a memory subdirectory.

    No-ops if there are no uncommitted changes. Uses a scoped git agent
    that only stages files under ``memories/<memory_type>/``.
    """
    if not await has_uncommitted_changes(agent_defaults.cwd):
        _log.debug("No uncommitted changes, skipping commit: type={type}", type=memory_type)
        return

    _log.info("Committing maintenance changes: type={type}", type=memory_type)

    prompt = (
        f"You are a git commit agent. Stage and commit memory maintenance changes.\n\n"
        f"## Instructions\n\n"
        f"1. Run `git status` to see all uncommitted changes.\n"
        f"2. Run `git add memories/{memory_type}/` to stage only the maintenance changes.\n"
        f"3. Run `git commit -m \"memory maintenance: {memory_type}\"`.\n\n"
        f"## Constraints\n\n"
        f"- Only use: `git status`, `git add`, `git commit`\n"
        f"- Do NOT use: `git push`, `git branch`, `git checkout`, `git reset`, "
        f"`git rebase`, `git merge`, `git stash`\n"
        f"- If there are no changes to commit, do nothing\n"
    )
    prompt = prompt.replace("$WORKSPACE", str(agent_defaults.cwd))

    await query_and_consume(
        prompt,
        agent_defaults,
        tools=GIT_TOOLS,
        allow=GIT_ALLOW,
        pre_tool_use_hooks=[GIT_BASH_HOOK],
        model=agent_defaults.processor_model,
    )


EPISODIC_MAINTENANCE_PROMPT = """\
You are a memory maintenance agent performing episodic memory consolidation.

## Directory

`$WORKSPACE/memories/episodic/`

## Pre-check

If the directory is empty or contains no `.md` files, stop immediately — \
nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with `.md` extension.
- Do not modify or delete files that are being actively written by other \
processes — if a file's content looks truncated or garbled, skip it.

## Time Tiers

Categorize every non-skipped file by its date (extracted from filename \
`YYYY-MM-DD.md` or from `YYYY-WNN.md` / `YYYY-MM.md` summary files):

### Tier 1: Recent (last {recent_days} days) — Clean only

- **Goal**: Reduce verbosity without losing substance.
- Remove repeated information that appears identically across entries.
- Remove excessive implementation detail (file lists, step-by-step code \
changes, routine activity status).
- Preserve: outcomes, decisions, new information, significant events.
- **NEVER delete files or remove substantive content from this tier.**
- If an entry is already clean and concise, leave it unchanged.

### Tier 2: Weekly consolidation ({recent_days} days – {weekly_threshold_months} months) \
— Weekly summaries

- Group daily files by ISO week (e.g., files from 2026-04-13 through \
2026-04-19 all belong to week 2026-W15).
- For each group, create a weekly summary file named `YYYY-WNN.md` \
(e.g., `2026-W15.md`).
- The summary should capture the high-level narrative and significant \
events of that week — decisions, outcomes, notable activities.
- Discard: routine implementation details, file change lists, repetitive \
status updates, step-by-step technical minutiae.
- If a weekly summary `YYYY-WNN.md` already exists from a previous run, \
merge new content into it — do not overwrite.
- After successful consolidation, delete the original daily files that \
were consolidated.
- Partial groups (e.g., only 2 days in a week) are consolidated into a \
single summary for that period.

### Tier 3: Monthly consolidation ({weekly_threshold_months} months – \
{monthly_threshold_months} months) — Monthly summaries

- Group all files (daily or weekly) by month.
- For each group, create a monthly summary file named `YYYY-MM.md` \
(e.g., `2026-04.md`).
- The summary should capture the most significant themes and events \
of that month at a high level.
- Discard: all routine detail — keep only notable outcomes, key decisions, \
and significant changes.
- If a monthly summary `YYYY-MM.md` already exists from a previous run, \
merge new content into it — do not overwrite.
- After successful consolidation, delete the original files.
- Partial months are consolidated into a single summary.

### Tier 4: Older than {monthly_threshold_months} months — Delete

- Delete all files older than {monthly_threshold_months} months.
- These entries have been through monthly consolidation already — \
anything remaining at this age is beyond the retention window.

## Idempotency

Before acting, check whether the work has already been done:
- If a weekly/monthly summary already exists and covers the files in its \
range, do not recreate it.
- If daily files in Tier 1 are already clean and concise, do not re-edit.
- If no files need processing, exit with no changes.

## Permissions

You can read files anywhere in the workspace. Edits and writes are \
restricted to `$WORKSPACE/memories/episodic/`. For Bash, read-only \
inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, \
`tail`, `wc`, `stat`) and `rm` for file deletion are allowed — other \
commands will be denied.
"""


async def episodic_maintenance_tick(
    agent_defaults: AgentDefaults,
    maintenance_settings: MaintenanceSettings,
) -> None:
    """Run episodic memory consolidation.

    Applies tiered maintenance: clean recent entries, consolidate into
    weekly and monthly summaries, delete very old entries.
    """
    scope = agent_defaults.cwd / "memories" / "episodic"
    rules = maintenance_allow_rules(scope)

    prompt = EPISODIC_MAINTENANCE_PROMPT.format(
        recent_days=maintenance_settings.recent_days,
        weekly_threshold_months=maintenance_settings.weekly_threshold_months,
        monthly_threshold_months=maintenance_settings.monthly_threshold_months,
    )
    prompt = prompt.replace("$WORKSPACE", str(agent_defaults.cwd))

    _log.info("Starting episodic maintenance tick")
    await query_and_consume(
        prompt,
        agent_defaults,
        tools=MAINTENANCE_TOOLS,
        allow=rules,
        pre_tool_use_hooks=[MAINTENANCE_BASH_HOOK],
        model=agent_defaults.processor_model,
    )
    await git_commit_memory_changes(agent_defaults, "episodic")
    _log.info("Episodic maintenance tick completed")


FACTS_MAINTENANCE_PROMPT = """\
You are a memory maintenance agent performing facts memory cleanup.

## Directory

`$WORKSPACE/memories/facts/`

## Pre-check

If the directory is empty or contains no `.md` files, stop immediately — \
nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with `.md` extension.

## Evaluation Criteria

Read all fact files and evaluate each for three issues:

### Staleness

An entry is stale when it describes a state that is no longer accurate:
- References to past dates or completed projects (e.g., "currently working \
on X" when X shipped months ago).
- Information contradicted by newer entries.
- Technical details that reference outdated tools, versions, or configurations.

For stale entries:
- If the entry has a newer, accurate replacement: remove the stale version.
- If the entry can be updated to reflect current state: edit it.
- If the entry describes a completed event with no ongoing relevance: remove it.

### Redundancy

Same information stated in different files:
- Keep the most complete and well-organized version.
- Remove the duplicate entries.

### Overlap

Related topics split across multiple files:
- When files cover overlapping subject areas, merge them into a single \
consolidated file.
- Choose the best filename from the originals, or create a more descriptive one.
- Remove the original files after merging.

## Deletion

- If a fact file is entirely obsolete (all entries stale, no useful content), \
delete it.
- Do NOT delete files that contain any useful or current information.

## Idempotency

Before acting, check whether work has already been done:
- If files are already deduplicated and consolidated, do not re-process.
- If no changes are needed, exit with no changes.

## Permissions

You can read files anywhere in the workspace. Edits and writes are \
restricted to `$WORKSPACE/memories/facts/`. For Bash, read-only \
inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, \
`tail`, `wc`, `stat`) and `rm` for file deletion are allowed — other \
commands will be denied.
"""


async def facts_maintenance_tick(
    agent_defaults: AgentDefaults,
) -> None:
    """Run facts memory cleanup.

    Evaluates fact files for staleness, redundancy, and overlap.
    """
    scope = agent_defaults.cwd / "memories" / "facts"
    rules = maintenance_allow_rules(scope)

    prompt = FACTS_MAINTENANCE_PROMPT.replace("$WORKSPACE", str(agent_defaults.cwd))

    _log.info("Starting facts maintenance tick")
    await query_and_consume(
        prompt,
        agent_defaults,
        tools=MAINTENANCE_TOOLS,
        allow=rules,
        pre_tool_use_hooks=[MAINTENANCE_BASH_HOOK],
        model=agent_defaults.processor_model,
    )
    await git_commit_memory_changes(agent_defaults, "facts")
    _log.info("Facts maintenance tick completed")


PREFERENCES_MAINTENANCE_PROMPT = """\
You are a memory maintenance agent performing preferences memory cleanup.

## Directory

`$WORKSPACE/memories/preferences/`

## Pre-check

If the directory is empty or contains no `.md` files, stop immediately — \
nothing to maintain.

## File Handling

- Skip empty (0-byte) or malformed files — do not attempt to process them.
- Only process files with `.md` extension.

## Evaluation Criteria

Read all preference files and evaluate each for two issues:

### Redundancy

Same preference stated multiple times across or within files:
- Keep the most complete and well-organized version.
- Remove the duplicate entries.

### Overlap

Related preferences split across multiple files:
- When files cover overlapping topics (e.g., coding style preferences split \
across "python-style.md" and "code-formatting.md"), merge them into a single \
consolidated file.
- Choose the best filename from the originals, or create a more descriptive one.
- Remove the original files after merging.

## Deletion

- If a preference file is entirely superseded (its preferences are all present \
in a newer, more complete file), delete it.
- If a preference file describes preferences the user no longer holds (e.g., \
contradicted by a newer entry), remove only the outdated entries — or delete \
the file if it becomes empty.
- Do NOT delete files that contain any current, unique preferences.

## Idempotency

Before acting, check whether work has already been done:
- If files are already deduplicated and consolidated, do not re-process.
- If no changes are needed, exit with no changes.

## Permissions

You can read files anywhere in the workspace. Edits and writes are \
restricted to `$WORKSPACE/memories/preferences/`. For Bash, read-only \
inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, \
`tail`, `wc`, `stat`) and `rm` for file deletion are allowed — other \
commands will be denied.
"""


async def preferences_maintenance_tick(
    agent_defaults: AgentDefaults,
) -> None:
    """Run preferences memory cleanup.

    Evaluates preference files for redundancy and overlap.
    """
    scope = agent_defaults.cwd / "memories" / "preferences"
    rules = maintenance_allow_rules(scope)

    prompt = PREFERENCES_MAINTENANCE_PROMPT.replace("$WORKSPACE", str(agent_defaults.cwd))

    _log.info("Starting preferences maintenance tick")
    await query_and_consume(
        prompt,
        agent_defaults,
        tools=MAINTENANCE_TOOLS,
        allow=rules,
        pre_tool_use_hooks=[MAINTENANCE_BASH_HOOK],
        model=agent_defaults.processor_model,
    )
    await git_commit_memory_changes(agent_defaults, "preferences")
    _log.info("Preferences maintenance tick completed")

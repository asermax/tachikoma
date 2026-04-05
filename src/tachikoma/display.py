"""Shared display utilities for rendering agent activity.

This module contains display-formatting logic shared between channels
(REPL, Telegram) for presenting agent events to users.
"""

from collections.abc import Callable
from os.path import basename
from typing import Any

from tachikoma.events import ToolActivity

# Live status line formatting (present-progressive)
TOOL_DISPLAY: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda inp: f"Reading {inp.get('file_path', '...')}",
    "Grep": lambda inp: f"Searching for '{inp.get('pattern', '...')}'",
    "Glob": lambda inp: f"Globbing {inp.get('pattern', '...')}",
    "Bash": lambda inp: (
        inp["description"]
        if inp.get("description")
        else f"Running: {inp.get('command', '...')}"
    ),
    "Edit": lambda inp: f"Editing {inp.get('file_path', '...')}",
    "Write": lambda inp: f"Writing {inp.get('file_path', '...')}",
    "Agent": lambda inp: f"Agent: {inp['description']}" if "description" in inp else "Agent...",
    "ToolSearch": lambda inp: f"Searching tools: {inp.get('query', '...')}",
}


# Per-tool summary formatters — present-progressive with basenames for brevity
# Falls back to generic placeholder when tool_input is missing expected keys
TOOL_SUMMARY: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda inp: (
        f"reading {basename(inp['file_path'])}" if "file_path" in inp else "reading a file"
    ),
    "Grep": lambda inp: (
        f"searching for '{inp['pattern']}'" if "pattern" in inp else "searching for a pattern"
    ),
    "Glob": lambda inp: (
        f"globbing '{inp['pattern']}'" if "pattern" in inp else "globbing a pattern"
    ),
    "Bash": lambda inp: _format_bash_summary(inp),
    "Edit": lambda inp: (
        f"editing {basename(inp['file_path'])}" if "file_path" in inp else "editing a file"
    ),
    "Write": lambda inp: (
        f"writing {basename(inp['file_path'])}" if "file_path" in inp else "writing a file"
    ),
    "Agent": lambda inp: (
        f"agent: {inp['description']}" if "description" in inp else "dispatched an agent"
    ),
    "ToolSearch": lambda _: "searching tools",
}


# Aggregated phrasing for tools with count > 2
_TOOL_AGGREGATE: dict[str, Callable[[int], str]] = {
    "Read": lambda c: f"reading {c} files",
    "Grep": lambda c: f"running {c} searches",
    "Glob": lambda c: f"running {c} glob searches",
    "Bash": lambda c: f"running {c} commands",
    "Edit": lambda c: f"editing {c} files",
    "Write": lambda c: f"writing {c} files",
    "Agent": lambda c: f"dispatching {c} agents",
    "ToolSearch": lambda c: f"running {c} tool searches",
}


def format_tool_name(name: str) -> str:
    """Format a raw tool name into a human-readable label.

    MCP tool names follow the pattern ``mcp__<server>__<tool_name>``.
    This function extracts the last segment, replaces underscores with
    spaces, and title-cases the result.  Non-MCP names pass through
    unchanged.
    """
    if not name.startswith("mcp__"):
        return name

    last_segment = name.rsplit("__", maxsplit=1)[-1]
    return last_segment.replace("_", " ").title()


def _format_bash_summary(
    tool_input: dict[str, Any],
    wrapper: Callable[[str], str] | None = None,
) -> str:
    """Format Bash tool summary with preference for description over command.

    Args:
        tool_input: The tool's input dict.
        wrapper: Optional function to wrap argument text (e.g. code_wrap for Telegram).
    """
    wrap = wrapper or (lambda s: s)

    if tool_input.get("description"):
        desc = tool_input["description"]
        formatted = desc[0].lower() + desc[1:] if len(desc) > 1 else desc.lower()
        return wrap(formatted)

    if "command" in tool_input:
        cmd = tool_input["command"]
        truncated = f"{cmd[:40]}..." if len(cmd) > 40 else cmd
        return f"running: {wrap(truncated)}"

    return "running a command"


def summarize_tool_activity(
    activities: list[ToolActivity],
    summary_map: dict[str, Callable[[dict[str, Any]], str]] | None = None,
) -> str:
    """Generate a human-readable summary from a list of tool activities.

    The summary is a single-line, capitalized verb-phrase describing what
    tools ran. Tools of the same type are aggregated (>2 uses count, ≤2 list
    individually). Multiple tool types are joined with commas and "and".

    Args:
        activities: List of ToolActivity events from a tool→text segment.
        summary_map: Optional per-tool formatters to use instead of TOOL_SUMMARY.
            Aggregation (_TOOL_AGGREGATE) is always shared.

    Returns:
        A summary string, or empty string if activities is empty.
    """
    if not activities:
        return ""

    effective_summary = summary_map if summary_map is not None else TOOL_SUMMARY

    # Group activities by tool_name, preserving first-seen order
    groups: dict[str, list[ToolActivity]] = {}
    for activity in activities:
        tool_name = activity.tool_name
        if tool_name not in groups:
            groups[tool_name] = []
        groups[tool_name].append(activity)

    # Build phrases for each group
    phrases: list[str] = []
    for tool_name, group_activities in groups.items():
        count = len(group_activities)

        if count > 2:
            # Use aggregated form
            if tool_name in _TOOL_AGGREGATE:
                phrases.append(_TOOL_AGGREGATE[tool_name](count))
            else:
                phrases.append(f"used {format_tool_name(tool_name)} {count} times")
        else:
            # List individually (count is 1 or 2)
            for activity in group_activities:
                if tool_name in effective_summary:
                    phrases.append(effective_summary[tool_name](activity.tool_input))
                else:
                    # Unknown tool fallback
                    phrases.append(f"used {format_tool_name(tool_name)}")

    # Cap at 5 phrases + "and more"
    if len(phrases) > 5:
        phrases = phrases[:5]
        phrases.append("and more")

    # Join phrases: 1 item → as-is; 2 items → "A and B"; 3+ → "A, B, and C"
    if len(phrases) == 1:
        result = phrases[0]
    elif len(phrases) == 2:
        result = f"{phrases[0]} and {phrases[1]}"
    else:
        result = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"

    # Capitalize first character
    return result[0].upper() + result[1:] if result else ""

"""Shared display utilities for rendering agent activity.

This module contains display-formatting logic shared between channels
(REPL, Telegram) for presenting agent events to users.
"""

from collections.abc import Callable
from os.path import basename
from typing import Any

from tachikoma.events import ToolActivity

# Live status line formatting (present-progressive, ellipsis)
TOOL_DISPLAY: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda inp: f"Reading {inp.get('file_path', '...')}...",
    "Grep": lambda inp: f"Searching for '{inp.get('pattern', '...')}'...",
    "Glob": lambda inp: f"Globbing {inp.get('pattern', '...')}...",
    "Bash": lambda inp: f"Running: {inp.get('command', '...')}",
    "ToolSearch": lambda inp: f"Searching tools: {inp.get('query', '...')}",
}


# Per-tool summary formatters — each returns a lowercase verb-phrase fragment
# Falls back to generic placeholder when tool_input is missing expected keys
TOOL_SUMMARY: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda inp: (
        f"read {basename(inp['file_path'])}" if "file_path" in inp else "read a file"
    ),
    "Grep": lambda inp: (
        f"searched for '{inp['pattern']}'" if "pattern" in inp else "searched for a pattern"
    ),
    "Glob": lambda inp: f"globbed '{inp['pattern']}'" if "pattern" in inp else "globbed a pattern",
    "Bash": lambda inp: _format_bash_summary(inp),
    "Edit": lambda inp: (
        f"edited {basename(inp['file_path'])}" if "file_path" in inp else "edited a file"
    ),
    "Write": lambda inp: (
        f"wrote {basename(inp['file_path'])}" if "file_path" in inp else "wrote a file"
    ),
    "ToolSearch": lambda _: "searched tools",
}


# Aggregated phrasing for tools with count > 2
_TOOL_AGGREGATE: dict[str, Callable[[int], str]] = {
    "Read": lambda c: f"read {c} files",
    "Grep": lambda c: f"ran {c} searches",
    "Glob": lambda c: f"ran {c} glob searches",
    "Bash": lambda c: f"ran {c} commands",
    "Edit": lambda c: f"edited {c} files",
    "Write": lambda c: f"wrote {c} files",
    "ToolSearch": lambda c: f"ran {c} tool searches",
}


def _format_bash_summary(tool_input: dict[str, Any]) -> str:
    """Format Bash tool summary with preference for description over command."""
    # Prefer description field (first char lowercased for sentence flow)
    if tool_input.get("description"):
        desc = tool_input["description"]
        # Lowercase first character, preserve rest (proper nouns, paths)
        return desc[0].lower() + desc[1:] if len(desc) > 1 else desc.lower()

    # Fall back to truncated command
    if "command" in tool_input:
        cmd = tool_input["command"]
        if len(cmd) > 40:
            return f"{cmd[:40]}..."
        return cmd

    # Final fallback
    return "ran a command"


def summarize_tool_activity(activities: list[ToolActivity]) -> str:
    """Generate a human-readable summary from a list of tool activities.

    The summary is a single-line, capitalized verb-phrase describing what
    tools ran. Tools of the same type are aggregated (>2 uses count, ≤2 list
    individually). Multiple tool types are joined with commas and "and".

    Args:
        activities: List of ToolActivity events from a tool→text segment.

    Returns:
        A summary string, or empty string if activities is empty.
    """
    if not activities:
        return ""

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
                phrases.append(f"used {tool_name} {count} times")
        else:
            # List individually (count is 1 or 2)
            for activity in group_activities:
                if tool_name in TOOL_SUMMARY:
                    phrases.append(TOOL_SUMMARY[tool_name](activity.tool_input))
                else:
                    # Unknown tool fallback
                    phrases.append(f"used {tool_name}")

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

"""Shared display utilities for rendering agent activity.

This module contains display-formatting logic shared between channels
(REPL, Telegram) for presenting agent events to users.
"""

from typing import Any

TOOL_DISPLAY: dict[str, Any] = {
    "Read": lambda inp: f"Reading {inp.get('file_path', '...')}...",
    "Grep": lambda inp: f"Searching for '{inp.get('pattern', '...')}'...",
    "Glob": lambda inp: f"Globbing {inp.get('pattern', '...')}...",
    "Bash": lambda inp: f"Running: {inp.get('command', '...')}",
    "ToolSearch": lambda inp: f"Searching tools: {inp.get('query', '...')}",
}

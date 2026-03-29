"""Pending signals tooling for CoreContextProcessor.

Provides SDK MCP tools for managing the pending signals file:
- add_pending_signal: Stage a new ambiguous signal
- remove_pending_signal: Remove promoted or stale signals by index
- parse_pending_signals: Parse file content into (date, text) tuples
- clean_pending_signals: Auto-cleanup expired entries

Also provides the create_pending_signals_server factory for use in forked sessions.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel

_log = logger.bind(component="pending_signals")


class RemovePendingSignalArgs(BaseModel):
    indices: list[int]


class AddPendingSignalArgs(BaseModel):
    signal: str


# File constants
PENDING_SIGNALS_FILENAME = "pending-signals.md"
PENDING_SIGNALS_HEADER = "# Pending Signals\n\n"

# Regex pattern for parsing dated entries
_ENTRY_PATTERN = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2})\*\*:\s*(.+)$", re.MULTILINE)


def _serialize_entries(entries: list[tuple[str, str]]) -> str:
    """Serialize (date, text) tuples into the pending signals file format."""
    return (
        PENDING_SIGNALS_HEADER + "\n".join(f"- **{d}**: {signal}" for d, signal in entries) + "\n"
    )


def parse_pending_signals(content: str) -> list[tuple[str, str]]:
    """Parse pending signals file content into (date, text) tuples.

    This is the public API for parsing pending signals, used by both
    the cleanup function and the processor for snapshot creation.

    Args:
        content: The raw content of the pending signals file.

    Returns:
        List of (date_str, signal_text) tuples. Empty list if no entries found.
    """
    return _ENTRY_PATTERN.findall(content)


def clean_pending_signals(data_dir: Path, max_age_days: int = 30) -> None:
    """Remove entries older than the threshold from the pending signals file.

    Runs before the forked session starts. Gracefully handles missing files
    and parse errors (logs warning and continues).

    Args:
        data_dir: Path to the .tachikoma directory.
        max_age_days: Maximum age in days before entries are removed. Defaults to 30.
    """
    file_path = data_dir / PENDING_SIGNALS_FILENAME

    # No-op if file doesn't exist
    if not file_path.exists():
        return

    try:
        content = file_path.read_text()
    except OSError as err:
        _log.warning(
            "Could not read pending signals file for cleanup: err={err}",
            err=str(err),
        )
        return

    # No-op if file is empty
    if not content.strip():
        return

    # Parse entries
    entries = parse_pending_signals(content)

    if not entries:
        # File has content but no parseable entries — log warning and continue
        _log.warning(
            "Pending signals file has content but no parseable entries: file={file}",
            file=str(file_path),
        )
        return

    # Calculate cutoff date using timedelta for correct date arithmetic
    today = date.today()
    cutoff = today - timedelta(days=max_age_days)

    # Filter out old entries
    filtered_entries: list[tuple[str, str]] = []
    for date_str, signal in entries:
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if entry_date >= cutoff:
                filtered_entries.append((date_str, signal))
        except ValueError:
            # Invalid date format — keep the entry (be conservative)
            _log.warning(
                "Could not parse date in pending signal: date={date}",
                date=date_str,
            )
            filtered_entries.append((date_str, signal))

    # If all entries expired, delete the file
    if not filtered_entries:
        try:
            file_path.unlink()
            _log.debug("Deleted empty pending signals file after cleanup")
        except OSError as err:
            _log.warning(
                "Could not delete pending signals file: err={err}",
                err=str(err),
            )
        return

    # Write back filtered content
    try:
        file_path.write_text(_serialize_entries(filtered_entries))
        removed_count = len(entries) - len(filtered_entries)
        if removed_count > 0:
            _log.debug(
                "Cleaned pending signals: removed={removed} remaining={remaining}",
                removed=removed_count,
                remaining=len(filtered_entries),
            )
    except OSError as err:
        _log.warning(
            "Could not write cleaned pending signals file: err={err}",
            err=str(err),
        )


async def handle_remove_pending_signal(
    indices: list[int],
    snapshot: list[tuple[str, str]],
    data_dir: Path,
) -> dict:
    """Handle remove_pending_signal tool logic.

    This function is extracted for testability. It removes signals by their
    1-based indices from the pre-fork snapshot.

    Args:
        indices: 1-based indices of signals to remove.
        snapshot: Pre-fork snapshot of pending signals as (date, text) tuples.
        data_dir: Path to the .tachikoma directory.

    Returns:
        Tool response dict with content and optional is_error flag.
    """
    # Empty list is a no-op success
    if not indices:
        return {"content": [{"type": "text", "text": "No signals removed (empty indices list)"}]}

    # Validate all indices are within range (all-or-nothing)
    max_index = len(snapshot)
    invalid_indices = [i for i in indices if i < 1 or i > max_index]

    if invalid_indices:
        valid_range = f"1-{max_index}" if max_index > 0 else "none (no signals exist)"
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Invalid indices: {invalid_indices} (valid range: {valid_range})",
                }
            ],
            "is_error": True,
        }

    # Convert 1-based to 0-based and compute entries to keep
    # (snapshot is immutable - we compute a filtered copy for writing)
    indices_to_remove = {i - 1 for i in indices}
    remaining_entries = [entry for i, entry in enumerate(snapshot) if i not in indices_to_remove]

    file_path = data_dir / PENDING_SIGNALS_FILENAME

    # If all signals removed, delete the file
    if not remaining_entries:
        try:
            file_path.unlink(missing_ok=True)
            _log.debug("Deleted pending signals file after removing all signals")
        except OSError as err:
            return {
                "content": [{"type": "text", "text": f"Error deleting file: {err}"}],
                "is_error": True,
            }

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Removed {len(indices)} signal(s). No pending signals remain.",
                }
            ]
        }

    # Write remaining entries back to file
    try:
        file_path.write_text(_serialize_entries(remaining_entries))
        _log.info(
            "Removed pending signals: count={count} remaining={remaining}",
            count=len(indices),
            remaining=len(remaining_entries),
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Removed {len(indices)} signal(s). {len(remaining_entries)} remaining."
                    ),
                }
            ]
        }
    except OSError as err:
        return {
            "content": [{"type": "text", "text": f"Error writing file: {err}"}],
            "is_error": True,
        }


async def handle_add_pending_signal(
    signal: str,
    data_dir: Path,
) -> dict:
    """Handle add_pending_signal tool logic.

    This function is extracted for testability. It appends a new signal
    entry with today's date.

    Args:
        signal: The signal text to add.
        data_dir: Path to the .tachikoma directory.

    Returns:
        Tool response dict with content and optional is_error flag.
    """
    if not signal:
        return {
            "content": [{"type": "text", "text": "Error: signal is required"}],
            "is_error": True,
        }

    file_path = data_dir / PENDING_SIGNALS_FILENAME
    today = date.today().isoformat()
    entry = f"- **{today}**: {signal}\n"

    try:
        # Check if file exists to determine if we need header
        if file_path.exists():
            # Append to existing file
            with file_path.open("a") as f:
                f.write(entry)
        else:
            # Create new file with header
            file_path.write_text(PENDING_SIGNALS_HEADER + entry)

        _log.info("Added pending signal: signal={signal}", signal=signal)

        return {"content": [{"type": "text", "text": f"Added pending signal dated {today}"}]}
    except OSError as err:
        return {
            "content": [{"type": "text", "text": f"Error writing file: {err}"}],
            "is_error": True,
        }


def create_pending_signals_server(
    data_dir: Path,
    snapshot: list[tuple[str, str]],
) -> McpSdkServerConfig:
    """Create an SDK MCP server with pending signals tools.

    The tools have closure over the data_dir path for file operations and
    the snapshot for index-based removal.

    Args:
        data_dir: Path to the .tachikoma directory.
        snapshot: Pre-fork snapshot of pending signals as (date, text) tuples.
            Used by the remove tool to resolve indices to entries.

    Returns:
        McpSdkServerConfig for use with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "remove_pending_signal",
        "Remove pending signal(s) by their prompt index (S1, S2, etc.). "
        "Use this when promoting a recurring signal to a context file update "
        "or when cleaning up stale/irrelevant signals. "
        "Indices refer to the S1..Sn numbers shown in your prompt.",
        RemovePendingSignalArgs.model_json_schema(),
    )
    async def remove_pending_signal(args: dict) -> dict:
        """Remove signals by their 1-based indices from the pre-fork snapshot."""
        parsed = RemovePendingSignalArgs.model_validate(args)
        return await handle_remove_pending_signal(
            indices=parsed.indices,
            snapshot=snapshot,
            data_dir=data_dir,
        )

    @tool(
        "add_pending_signal",
        "Stage a new ambiguous signal for future recurrence detection. "
        "Adds the signal with today's date to the pending signals file. "
        "Use this for one-off observations that might become patterns if they recur.",
        AddPendingSignalArgs.model_json_schema(),
    )
    async def add_pending_signal(args: dict) -> dict:
        """Append a new signal entry with today's date."""
        parsed = AddPendingSignalArgs.model_validate(args)
        return await handle_add_pending_signal(
            signal=parsed.signal,
            data_dir=data_dir,
        )

    return create_sdk_mcp_server(
        name="pending-signals",
        version="1.0.0",
        tools=[remove_pending_signal, add_pending_signal],
    )

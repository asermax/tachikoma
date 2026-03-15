"""Pending signals tooling for CoreContextProcessor.

Provides SDK MCP tools for managing the pending signals file:
- read_pending_signals: Read the full list with dates
- add_pending_signal: Append a new entry with current date
- clean_pending_signals: Auto-cleanup expired entries

Also provides the create_pending_signals_server factory for use in forked sessions.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger

_log = logger.bind(component="pending_signals")

# File constants
PENDING_SIGNALS_FILENAME = "pending-signals.md"
PENDING_SIGNALS_HEADER = "# Pending Signals\n\n"

# Regex pattern for parsing dated entries
_ENTRY_PATTERN = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2})\*\*:\s*(.+)$", re.MULTILINE)


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
    entries = _ENTRY_PATTERN.findall(content)

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
    new_content = PENDING_SIGNALS_HEADER + "\n".join(
        f"- **{date}**: {signal}" for date, signal in filtered_entries
    )

    try:
        file_path.write_text(new_content)
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


def create_pending_signals_server(data_dir: Path) -> McpSdkServerConfig:
    """Create an SDK MCP server with pending signals tools.

    The tools have closure over the data_dir path for file operations.

    Args:
        data_dir: Path to the .tachikoma directory.

    Returns:
        McpSdkServerConfig for use with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "read_pending_signals",
        "Read all pending signals with their dates. Returns empty string if no signals exist.",
        {},  # Empty input schema — no arguments
    )
    async def read_pending_signals(args: dict) -> dict:
        """Read the pending signals file contents."""
        file_path = data_dir / PENDING_SIGNALS_FILENAME

        try:
            content = file_path.read_text()
            return {"content": [{"type": "text", "text": content}]}
        except FileNotFoundError:
            return {"content": [{"type": "text", "text": ""}]}
        except OSError as err:
            return {
                "content": [{"type": "text", "text": f"Error reading file: {err}"}],
                "is_error": True,
            }

    @tool(
        "add_pending_signal",
        "Add a new pending signal with today's date.",
        {"signal": str},  # Input schema
    )
    async def add_pending_signal(args: dict) -> dict:
        """Append a new signal entry with today's date."""
        signal = args.get("signal", "")
        if not signal:
            return {
                "content": [{"type": "text", "text": "Error: signal is required"}],
                "is_error": True,
            }

        file_path = data_dir / PENDING_SIGNALS_FILENAME
        today = datetime.now().strftime("%Y-%m-%d")
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

            return {
                "content": [
                    {"type": "text", "text": f"Added pending signal dated {today}"}
                ]
            }
        except OSError as err:
            return {
                "content": [{"type": "text", "text": f"Error writing file: {err}"}],
                "is_error": True,
            }

    return create_sdk_mcp_server(
        name="pending-signals",
        version="1.0.0",
        tools=[read_pending_signals, add_pending_signal],
    )


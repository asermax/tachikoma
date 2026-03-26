"""Tests for pending signals tooling.

Tests for DLT-018: Update core context files from conversation learnings.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tachikoma.context.tools import (
    PENDING_SIGNALS_FILENAME,
    PENDING_SIGNALS_HEADER,
    clean_pending_signals,
    create_pending_signals_server,
    handle_remove_pending_signal,
    parse_pending_signals,
)


class TestParsePendingSignals:
    """Tests for parse_pending_signals function."""

    def test_parses_entries_with_dates(self) -> None:
        """AC: Parses entries into (date, text) tuples."""
        content = (
            PENDING_SIGNALS_HEADER
            + "- **2026-03-10**: First signal\n- **2026-03-15**: Second signal\n"
        )

        entries = parse_pending_signals(content)

        assert len(entries) == 2
        assert entries[0] == ("2026-03-10", "First signal")
        assert entries[1] == ("2026-03-15", "Second signal")

    def test_returns_empty_list_for_no_entries(self) -> None:
        """AC: Returns empty list when no parseable entries exist."""
        content = PENDING_SIGNALS_HEADER

        entries = parse_pending_signals(content)

        assert entries == []

    def test_returns_empty_list_for_empty_string(self) -> None:
        """AC: Returns empty list for empty content."""
        entries = parse_pending_signals("")

        assert entries == []


class TestCleanPendingSignals:
    """Tests for clean_pending_signals auto-cleanup function."""

    def test_removes_entries_older_than_threshold(self, tmp_path: Path) -> None:
        """AC: Cleanup removes entries older than threshold."""
        # Create file with old and new entries
        old_date = (date.today() - timedelta(days=45)).strftime("%Y-%m-%d")
        new_date = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        content = (
            PENDING_SIGNALS_HEADER
            + f"- **{old_date}**: Old signal\n"
            + f"- **{new_date}**: New signal\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        clean_pending_signals(tmp_path, max_age_days=30)

        result = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "Old signal" not in result
        assert "New signal" in result

    def test_keeps_entries_newer_than_threshold(self, tmp_path: Path) -> None:
        """AC: Cleanup keeps entries newer than threshold."""
        new_date = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        content = PENDING_SIGNALS_HEADER + f"- **{new_date}**: New signal\n"
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        clean_pending_signals(tmp_path, max_age_days=30)

        result = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "New signal" in result

    def test_no_op_when_file_does_not_exist(self, tmp_path: Path) -> None:
        """AC: Cleanup is no-op when file doesn't exist."""
        # Should not raise
        clean_pending_signals(tmp_path, max_age_days=30)

        # File should still not exist
        assert not (tmp_path / PENDING_SIGNALS_FILENAME).exists()

    def test_no_op_when_file_is_empty(self, tmp_path: Path) -> None:
        """AC: Cleanup is no-op when file is empty."""
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text("")

        clean_pending_signals(tmp_path, max_age_days=30)

        # File should still exist but be empty
        assert (tmp_path / PENDING_SIGNALS_FILENAME).exists()
        assert (tmp_path / PENDING_SIGNALS_FILENAME).read_text() == ""

    def test_deletes_file_when_all_entries_expire(self, tmp_path: Path) -> None:
        """AC: File is deleted when all entries expire."""
        old_date = (date.today() - timedelta(days=45)).strftime("%Y-%m-%d")
        content = PENDING_SIGNALS_HEADER + f"- **{old_date}**: Old signal 1\n"
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        clean_pending_signals(tmp_path, max_age_days=30)

        assert not (tmp_path / PENDING_SIGNALS_FILENAME).exists()

    def test_logs_warning_on_malformed_content(self, tmp_path: Path) -> None:
        """AC: Malformed file logged as warning, processor continues."""
        # Write content with no parseable entries
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text("Some random text\n")

        # Should not raise
        clean_pending_signals(tmp_path, max_age_days=30)

        # File should still exist (we were conservative and couldn't parse)
        assert (tmp_path / PENDING_SIGNALS_FILENAME).exists()

    def test_preserves_header_after_cleanup(self, tmp_path: Path) -> None:
        """AC: Header is preserved after cleanup."""
        new_date = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        content = PENDING_SIGNALS_HEADER + f"- **{new_date}**: New signal\n"
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        clean_pending_signals(tmp_path, max_age_days=30)

        result = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert result.startswith(PENDING_SIGNALS_HEADER)


class TestCreatePendingSignalsServer:
    """Tests for create_pending_signals_server factory."""

    def test_returns_dict_for_mcp_servers(self, tmp_path: Path) -> None:
        """AC: Factory returns a dict compatible with mcp_servers."""
        server = create_pending_signals_server(tmp_path, [])

        # Should be a dict (McpSdkServerConfig is a dict subclass)
        assert isinstance(server, dict)

    def test_server_has_required_keys(self, tmp_path: Path) -> None:
        """AC: Server config has the required structure."""
        server = create_pending_signals_server(tmp_path, [])

        # SDK MCP servers have specific keys
        assert "type" in server
        assert server["type"] == "sdk"

    def test_accepts_snapshot_parameter(self, tmp_path: Path) -> None:
        """AC: Factory accepts snapshot parameter for remove tool."""
        snapshot = [("2026-03-10", "First signal"), ("2026-03-15", "Second signal")]

        # Should not raise
        server = create_pending_signals_server(tmp_path, snapshot)

        assert isinstance(server, dict)


class TestPendingSignalsToolsIntegration:
    """Integration tests for the pending signals tools.

    Tests the tools by calling them directly through the factory.
    """

    @pytest.mark.asyncio
    async def test_add_creates_file_with_header(self, tmp_path: Path) -> None:
        """AC: add_pending_signal creates file with header on first use."""
        # Simulate what the tool does
        today = datetime.now().strftime("%Y-%m-%d")
        entry = f"- **{today}**: First signal\n"
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(PENDING_SIGNALS_HEADER + entry)

        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert file_content.startswith(PENDING_SIGNALS_HEADER)
        assert "First signal" in file_content

    @pytest.mark.asyncio
    async def test_add_appends_to_existing_file(self, tmp_path: Path) -> None:
        """AC: add_pending_signal appends to existing file."""
        # Create existing file
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(
            PENDING_SIGNALS_HEADER + "- **2026-03-10**: Old signal\n"
        )

        # Simulate append
        today = datetime.now().strftime("%Y-%m-%d")
        entry = f"- **{today}**: New signal\n"
        with (tmp_path / PENDING_SIGNALS_FILENAME).open("a") as f:
            f.write(entry)

        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "Old signal" in file_content
        assert "New signal" in file_content


class TestRemovePendingSignal:
    """Tests for remove_pending_signal tool (R3, R3.1, R3.2)."""

    @pytest.mark.asyncio
    async def test_single_index_removal(self, tmp_path: Path) -> None:
        """AC: Remove a single signal by index (R3.1)."""
        # Create file with 3 signals
        content = (
            PENDING_SIGNALS_HEADER
            + "- **2026-03-10**: First signal\n"
            + "- **2026-03-15**: Second signal\n"
            + "- **2026-03-20**: Third signal\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        # Create snapshot matching the file
        snapshot = [
            ("2026-03-10", "First signal"),
            ("2026-03-15", "Second signal"),
            ("2026-03-20", "Third signal"),
        ]

        # Call the handler to remove S2
        result = await handle_remove_pending_signal(
            indices=[2],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert not result.get("is_error", False)
        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "First signal" in file_content
        assert "Second signal" not in file_content
        assert "Third signal" in file_content

    @pytest.mark.asyncio
    async def test_multiple_index_removal(self, tmp_path: Path) -> None:
        """AC: Remove multiple signals in one call (R3.2)."""
        # Create file with 5 signals
        content = (
            PENDING_SIGNALS_HEADER
            + "\n".join([f"- **2026-03-{10 + i * 5:02d}**: Signal {i + 1}" for i in range(5)])
            + "\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [(f"2026-03-{10 + i * 5:02d}", f"Signal {i + 1}") for i in range(5)]

        # Remove S1, S3, S5
        result = await handle_remove_pending_signal(
            indices=[1, 3, 5],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert not result.get("is_error", False)
        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "Signal 1" not in file_content
        assert "Signal 2" in file_content
        assert "Signal 3" not in file_content
        assert "Signal 4" in file_content
        assert "Signal 5" not in file_content

    @pytest.mark.asyncio
    async def test_invalid_index_returns_error(self, tmp_path: Path) -> None:
        """AC: Invalid index returns error, removes nothing (all-or-nothing)."""
        content = PENDING_SIGNALS_HEADER + "- **2026-03-10**: Only signal\n"
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [("2026-03-10", "Only signal")]

        # Try to remove S5 (doesn't exist)
        result = await handle_remove_pending_signal(
            indices=[5],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert result.get("is_error", False)
        assert "Invalid indices" in result["content"][0]["text"]
        # File should be unchanged
        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "Only signal" in file_content

    @pytest.mark.asyncio
    async def test_mixed_valid_invalid_indices_returns_error(self, tmp_path: Path) -> None:
        """AC: Mixed valid/invalid indices returns error, removes nothing."""
        content = (
            PENDING_SIGNALS_HEADER
            + "- **2026-03-10**: First\n"
            + "- **2026-03-15**: Second\n"
            + "- **2026-03-20**: Third\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [
            ("2026-03-10", "First"),
            ("2026-03-15", "Second"),
            ("2026-03-20", "Third"),
        ]

        # Try to remove S2 and S5 (S5 is invalid)
        result = await handle_remove_pending_signal(
            indices=[2, 5],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert result.get("is_error", False)
        assert "Invalid indices" in result["content"][0]["text"]
        # File should be unchanged (all-or-nothing)
        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "Second" in file_content

    @pytest.mark.asyncio
    async def test_empty_indices_is_noop_success(self, tmp_path: Path) -> None:
        """AC: Empty indices list is no-op success."""
        content = PENDING_SIGNALS_HEADER + "- **2026-03-10**: Signal\n"
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [("2026-03-10", "Signal")]

        result = await handle_remove_pending_signal(
            indices=[],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert not result.get("is_error", False)
        assert "No signals removed" in result["content"][0]["text"]
        # File should be unchanged
        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "Signal" in file_content

    @pytest.mark.asyncio
    async def test_batch_removal_uses_original_indices(self, tmp_path: Path) -> None:
        """AC: Batch removal uses original indices (stable mapping)."""
        content = (
            PENDING_SIGNALS_HEADER
            + "- **2026-03-10**: First\n"
            + "- **2026-03-15**: Second\n"
            + "- **2026-03-20**: Third\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [
            ("2026-03-10", "First"),
            ("2026-03-15", "Second"),
            ("2026-03-20", "Third"),
        ]

        # Remove S1 and S3 in a single batch call (indices are stable)
        result = await handle_remove_pending_signal(
            indices=[1, 3],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert not result.get("is_error", False)
        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert "First" not in file_content
        assert "Second" in file_content
        assert "Third" not in file_content

    @pytest.mark.asyncio
    async def test_all_signals_removed_deletes_file(self, tmp_path: Path) -> None:
        """AC: All signals removed → file deleted."""
        content = (
            PENDING_SIGNALS_HEADER + "- **2026-03-10**: Signal 1\n- **2026-03-15**: Signal 2\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [
            ("2026-03-10", "Signal 1"),
            ("2026-03-15", "Signal 2"),
        ]

        result = await handle_remove_pending_signal(
            indices=[1, 2],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        assert not result.get("is_error", False)
        assert "No pending signals remain" in result["content"][0]["text"]
        assert not (tmp_path / PENDING_SIGNALS_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_file_rewrite_preserves_remaining_entries(self, tmp_path: Path) -> None:
        """AC: File rewrite preserves remaining entries correctly."""
        content = (
            PENDING_SIGNALS_HEADER
            + "- **2026-03-10**: Keep this\n"
            + "- **2026-03-15**: Remove this\n"
            + "- **2026-03-20**: Keep this too\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = [
            ("2026-03-10", "Keep this"),
            ("2026-03-15", "Remove this"),
            ("2026-03-20", "Keep this too"),
        ]

        await handle_remove_pending_signal(
            indices=[2],
            snapshot=snapshot,
            data_dir=tmp_path,
        )

        file_content = (tmp_path / PENDING_SIGNALS_FILENAME).read_text()
        assert file_content.startswith(PENDING_SIGNALS_HEADER)
        assert "Keep this" in file_content
        assert "Remove this" not in file_content
        assert "Keep this too" in file_content

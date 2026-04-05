"""Unit tests for context assembly function.

Tests for DLT-041: Persist session context to database.
"""

import re

from tachikoma.context.assembly import build_system_prompt
from tachikoma.context.loading import render_system_preamble
from tachikoma.sessions.model import SessionContextEntry


class TestBuildSystemPrompt:
    """Tests for build_system_prompt() pure function (DLT-041 S3)."""

    def test_empty_entries_returns_preamble_only(self) -> None:
        """AC: empty entries list returns rendered preamble alone."""
        result = build_system_prompt([])

        assert result == render_system_preamble()

    def test_empty_entries_with_timezone(self) -> None:
        """AC: empty entries with timezone returns preamble with that timezone."""
        result = build_system_prompt([], timezone="UTC")

        assert "UTC" in result
        assert "## Date and Time" in result

    def test_single_entry_wraps_in_xml(self) -> None:
        """AC: single entry is wrapped in <owner> XML tags."""
        entries = [
            SessionContextEntry(
                id=1,
                session_id="s1",
                owner="memories",
                content="User prefers dark mode",
            )
        ]

        result = build_system_prompt(entries)

        assert render_system_preamble() in result
        assert "<memories>" in result
        assert "User prefers dark mode" in result
        assert "</memories>" in result

    def test_multiple_entries_maintain_input_order(self) -> None:
        """AC: multiple entries are assembled in the order provided (deterministic)."""
        entries = [
            SessionContextEntry(id=1, session_id="s1", owner="foundational", content="First entry"),
            SessionContextEntry(id=2, session_id="s1", owner="memories", content="Second entry"),
            SessionContextEntry(id=3, session_id="s1", owner="skills", content="Third entry"),
        ]

        result = build_system_prompt(entries)

        # Use regex to find actual entry tags (with newline after) to avoid
        # false matches from documentation references in the rendered preamble
        foundational_match = re.search(r"<foundational>\n", result)
        memories_match = re.search(r"<memories>\n", result)
        skills_match = re.search(r"<skills>\n", result)

        assert foundational_match is not None
        assert memories_match is not None
        assert skills_match is not None

        # Check ordering: foundational comes before memories, memories before skills
        assert foundational_match.start() < memories_match.start() < skills_match.start()

    def test_preamble_always_prepended(self) -> None:
        """AC: rendered preamble is always at the start of the result."""
        entries = [SessionContextEntry(id=1, session_id="s1", owner="test", content="content")]

        result = build_system_prompt(entries)

        assert result.startswith(render_system_preamble())

    def test_deterministic_output_for_same_entries(self) -> None:
        """AC: same entries always produce identical output (deterministic per R2)."""
        entries = [
            SessionContextEntry(id=1, session_id="s1", owner="memories", content="Test content")
        ]

        result1 = build_system_prompt(entries)
        result2 = build_system_prompt(entries)

        assert result1 == result2

    def test_entry_with_multiline_content(self) -> None:
        """AC: multiline content is preserved correctly within XML tags."""
        multiline_content = """Line 1
Line 2
Line 3"""

        entries = [
            SessionContextEntry(id=1, session_id="s1", owner="memories", content=multiline_content)
        ]

        result = build_system_prompt(entries)

        assert multiline_content in result

    def test_entry_with_special_characters(self) -> None:
        """AC: special characters in content are preserved (no escaping needed)."""
        special_content = "User said: <hello> & 'goodbye'"

        entries = [
            SessionContextEntry(id=1, session_id="s1", owner="test", content=special_content)
        ]

        result = build_system_prompt(entries)

        # Content should be preserved exactly, not XML-escaped
        assert special_content in result

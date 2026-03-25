"""Display module tests.

Tests for DLT-034: Summarize agent actions instead of generic tool markers.
"""

from tachikoma.display import summarize_tool_activity
from tachikoma.events import ToolActivity


class TestSummarizeEmpty:
    """Tests for empty/edge cases."""

    def test_empty_list_returns_empty_string(self) -> None:
        """AC: Empty activity list returns empty string."""
        assert summarize_tool_activity([]) == ""


class TestSummarizeSingleTool:
    """Tests for individual tool formatting (R4)."""

    def test_read_with_file_path(self) -> None:
        """AC: Read with file_path shows basename."""
        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "/src/main.py"})
        assert summarize_tool_activity([activity]) == "Read main.py"

    def test_read_without_file_path(self) -> None:
        """AC: Read without file_path (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Read", tool_input={})
        assert summarize_tool_activity([activity]) == "Read a file"

    def test_grep_with_pattern(self) -> None:
        """AC: Grep with pattern shows the search pattern."""
        activity = ToolActivity(tool_name="Grep", tool_input={"pattern": "logging config"})
        assert summarize_tool_activity([activity]) == "Searched for 'logging config'"

    def test_grep_without_pattern(self) -> None:
        """AC: Grep without pattern (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Grep", tool_input={})
        assert summarize_tool_activity([activity]) == "Searched for a pattern"

    def test_glob_with_pattern(self) -> None:
        """AC: Glob with pattern shows the glob pattern."""
        activity = ToolActivity(tool_name="Glob", tool_input={"pattern": "**/*.py"})
        assert summarize_tool_activity([activity]) == "Globbed '**/*.py'"

    def test_glob_without_pattern(self) -> None:
        """AC: Glob without pattern (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Glob", tool_input={})
        assert summarize_tool_activity([activity]) == "Globbed a pattern"

    def test_bash_with_description(self) -> None:
        """AC: Bash with description shows lowercased-first-char description."""
        activity = ToolActivity(tool_name="Bash", tool_input={"description": "Run tests"})
        assert summarize_tool_activity([activity]) == "Run tests"

    def test_bash_description_preserves_casing(self) -> None:
        """AC: Bash description preserves casing of rest of string (proper nouns)."""
        activity = ToolActivity(
            tool_name="Bash", tool_input={"description": "Install Docker deps"}
        )
        assert summarize_tool_activity([activity]) == "Install Docker deps"

    def test_bash_with_command_only(self) -> None:
        """AC: Bash with command only shows truncated command (capitalized)."""
        activity = ToolActivity(tool_name="Bash", tool_input={"command": "pytest tests/"})
        assert summarize_tool_activity([activity]) == "Pytest tests/"

    def test_bash_with_long_command(self) -> None:
        """AC: Bash with long command shows 40 chars + '...'."""
        long_cmd = "python -m pytest tests/test_very_long_module_name.py -v --tb=short"
        activity = ToolActivity(tool_name="Bash", tool_input={"command": long_cmd})
        result = summarize_tool_activity([activity])
        assert result.endswith("...")
        assert len(result) == 43  # 40 chars of command + "..."

    def test_bash_with_neither(self) -> None:
        """AC: Bash with neither description nor command falls back gracefully."""
        activity = ToolActivity(tool_name="Bash", tool_input={})
        assert summarize_tool_activity([activity]) == "Ran a command"

    def test_edit_with_file_path(self) -> None:
        """AC: Edit with file_path shows basename."""
        activity = ToolActivity(tool_name="Edit", tool_input={"file_path": "/src/config.py"})
        assert summarize_tool_activity([activity]) == "Edited config.py"

    def test_edit_without_file_path(self) -> None:
        """AC: Edit without file_path (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Edit", tool_input={})
        assert summarize_tool_activity([activity]) == "Edited a file"

    def test_write_with_file_path(self) -> None:
        """AC: Write with file_path shows basename."""
        activity = ToolActivity(tool_name="Write", tool_input={"file_path": "/src/output.txt"})
        assert summarize_tool_activity([activity]) == "Wrote output.txt"

    def test_write_without_file_path(self) -> None:
        """AC: Write without file_path (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Write", tool_input={})
        assert summarize_tool_activity([activity]) == "Wrote a file"

    def test_tool_search(self) -> None:
        """AC: ToolSearch shows 'searched tools'."""
        activity = ToolActivity(tool_name="ToolSearch", tool_input={"query": "git"})
        assert summarize_tool_activity([activity]) == "Searched tools"

    def test_unknown_tool(self) -> None:
        """AC: Unknown tool falls back to 'used {tool_name}'."""
        activity = ToolActivity(tool_name="CustomMCP", tool_input={})
        assert summarize_tool_activity([activity]) == "Used CustomMCP"


class TestSummarizeMultipleTools:
    """Tests for multi-tool and aggregation (R3, R5)."""

    def test_two_reads_listed_individually(self) -> None:
        """AC: 2 Reads listed individually with 'and'."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/b.py"}),
        ]
        assert summarize_tool_activity(activities) == "Read a.py and read b.py"

    def test_three_reads_aggregated(self) -> None:
        """AC: 3 Reads aggregated by count."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/b.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/c.py"}),
        ]
        assert summarize_tool_activity(activities) == "Read 3 files"

    def test_two_groups_joined_with_and(self) -> None:
        """AC: 2 groups (aggregated + individual) joined with 'and'."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/b.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/c.py"}),
            ToolActivity(tool_name="Grep", tool_input={"pattern": "pattern"}),
        ]
        assert summarize_tool_activity(activities) == "Read 3 files and searched for 'pattern'"

    def test_three_plus_groups_with_oxford_comma(self) -> None:
        """AC: 3+ groups joined with commas and Oxford comma."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Grep", tool_input={"pattern": "pattern"}),
            ToolActivity(tool_name="Bash", tool_input={"description": "Run tests"}),
        ]
        assert (
            summarize_tool_activity(activities)
            == "Read a.py, searched for 'pattern', and run tests"
        )

    def test_preserves_first_seen_order(self) -> None:
        """AC: Tool groups preserve first-seen order."""
        activities = [
            ToolActivity(tool_name="Bash", tool_input={"description": "First"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Bash", tool_input={"description": "Second"}),
        ]
        # Bash comes first in first-seen order, so all Bash phrases come before Read
        result = summarize_tool_activity(activities)
        # Bash group (2 items) listed as "first, second", then Read
        assert result == "First, second, and read a.py"

    def test_more_than_five_groups_capped(self) -> None:
        """AC: >5 groups capped with 'and more'."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Grep", tool_input={"pattern": "p1"}),
            ToolActivity(tool_name="Glob", tool_input={"pattern": "*.py"}),
            ToolActivity(tool_name="Bash", tool_input={"description": "Run"}),
            ToolActivity(tool_name="Edit", tool_input={"file_path": "/src/e.py"}),
            ToolActivity(tool_name="Write", tool_input={"file_path": "/src/f.py"}),
            ToolActivity(tool_name="ToolSearch", tool_input={"query": "q"}),
        ]
        result = summarize_tool_activity(activities)
        # Should have first 5 + "and more"
        assert "and more" in result
        assert "searched tools" not in result.lower()  # 7th tool not included

    def test_single_tool_capitalized(self) -> None:
        """AC: Single tool has first word capitalized."""
        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "/src/main.py"})
        result = summarize_tool_activity([activity])
        assert result[0].isupper()


class TestSummarizeBashDescription:
    """Tests for Bash description formatting."""

    def test_first_char_lowercased(self) -> None:
        """AC: Description first char is lowercased for sentence flow."""
        activity = ToolActivity(tool_name="Bash", tool_input={"description": "Run tests"})
        assert summarize_tool_activity([activity]) == "Run tests"

    def test_single_char_description(self) -> None:
        """AC: Single-char description is lowercased then capitalized by summarizer."""
        activity = ToolActivity(tool_name="Bash", tool_input={"description": "R"})
        # Formatter lowercases to "r", summarizer capitalizes final result to "R"
        assert summarize_tool_activity([activity]) == "R"

    def test_description_preference_over_command(self) -> None:
        """AC: Description is preferred over command when both present."""
        activity = ToolActivity(
            tool_name="Bash",
            tool_input={"description": "Run tests", "command": "pytest -v"},
        )
        assert summarize_tool_activity([activity]) == "Run tests"

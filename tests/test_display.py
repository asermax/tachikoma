"""Display module tests.

Tests for DLT-034: Summarize agent actions instead of generic tool markers.
"""

from tachikoma.display import format_tool_name, summarize_tool_activity
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
        assert summarize_tool_activity([activity]) == "Reading main.py"

    def test_read_without_file_path(self) -> None:
        """AC: Read without file_path (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Read", tool_input={})
        assert summarize_tool_activity([activity]) == "Reading a file"

    def test_grep_with_pattern(self) -> None:
        """AC: Grep with pattern shows the search pattern."""
        activity = ToolActivity(tool_name="Grep", tool_input={"pattern": "logging config"})
        assert summarize_tool_activity([activity]) == "Searching for 'logging config'"

    def test_grep_without_pattern(self) -> None:
        """AC: Grep without pattern (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Grep", tool_input={})
        assert summarize_tool_activity([activity]) == "Searching for a pattern"

    def test_glob_with_pattern(self) -> None:
        """AC: Glob with pattern shows the glob pattern."""
        activity = ToolActivity(tool_name="Glob", tool_input={"pattern": "**/*.py"})
        assert summarize_tool_activity([activity]) == "Globbing '**/*.py'"

    def test_glob_without_pattern(self) -> None:
        """AC: Glob without pattern (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Glob", tool_input={})
        assert summarize_tool_activity([activity]) == "Globbing a pattern"

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
        """AC: Bash with command only shows 'running: command'."""
        activity = ToolActivity(tool_name="Bash", tool_input={"command": "pytest tests/"})
        assert summarize_tool_activity([activity]) == "Running: pytest tests/"

    def test_bash_with_long_command(self) -> None:
        """AC: Bash with long command shows 'running: ' + 40 chars + '...'."""
        long_cmd = "python -m pytest tests/test_very_long_module_name.py -v --tb=short"
        activity = ToolActivity(tool_name="Bash", tool_input={"command": long_cmd})
        result = summarize_tool_activity([activity])
        assert result.startswith("Running: ")
        assert result.endswith("...")

    def test_bash_with_neither(self) -> None:
        """AC: Bash with neither description nor command falls back gracefully."""
        activity = ToolActivity(tool_name="Bash", tool_input={})
        assert summarize_tool_activity([activity]) == "Running a command"

    def test_edit_with_file_path(self) -> None:
        """AC: Edit with file_path shows basename."""
        activity = ToolActivity(tool_name="Edit", tool_input={"file_path": "/src/config.py"})
        assert summarize_tool_activity([activity]) == "Editing config.py"

    def test_edit_without_file_path(self) -> None:
        """AC: Edit without file_path (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Edit", tool_input={})
        assert summarize_tool_activity([activity]) == "Editing a file"

    def test_write_with_file_path(self) -> None:
        """AC: Write with file_path shows basename."""
        activity = ToolActivity(tool_name="Write", tool_input={"file_path": "/src/output.txt"})
        assert summarize_tool_activity([activity]) == "Writing output.txt"

    def test_write_without_file_path(self) -> None:
        """AC: Write without file_path (malformed) falls back gracefully."""
        activity = ToolActivity(tool_name="Write", tool_input={})
        assert summarize_tool_activity([activity]) == "Writing a file"

    def test_tool_search(self) -> None:
        """AC: ToolSearch shows 'searching tools'."""
        activity = ToolActivity(tool_name="ToolSearch", tool_input={"query": "git"})
        assert summarize_tool_activity([activity]) == "Searching tools"

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
        assert summarize_tool_activity(activities) == "Reading a.py and reading b.py"

    def test_three_reads_aggregated(self) -> None:
        """AC: 3 Reads aggregated by count."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/b.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/c.py"}),
        ]
        assert summarize_tool_activity(activities) == "Reading 3 files"

    def test_two_groups_joined_with_and(self) -> None:
        """AC: 2 groups (aggregated + individual) joined with 'and'."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/b.py"}),
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/c.py"}),
            ToolActivity(tool_name="Grep", tool_input={"pattern": "pattern"}),
        ]
        assert (
            summarize_tool_activity(activities)
            == "Reading 3 files and searching for 'pattern'"
        )

    def test_three_plus_groups_with_oxford_comma(self) -> None:
        """AC: 3+ groups joined with commas and Oxford comma."""
        activities = [
            ToolActivity(tool_name="Read", tool_input={"file_path": "/src/a.py"}),
            ToolActivity(tool_name="Grep", tool_input={"pattern": "pattern"}),
            ToolActivity(tool_name="Bash", tool_input={"description": "Run tests"}),
        ]
        assert (
            summarize_tool_activity(activities)
            == "Reading a.py, searching for 'pattern', and run tests"
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
        assert result == "First, second, and reading a.py"

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
        assert "searching tools" not in result.lower()  # 7th tool not included

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


class TestSummarizeCapitalization:
    """Tests for capitalization of the final summary string."""

    def test_single_tool_capitalized(self) -> None:
        """AC: Single tool has first word capitalized."""
        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "/src/main.py"})
        result = summarize_tool_activity([activity])
        assert result[0].isupper()


class TestFormatToolName:
    """Tests for format_tool_name: MCP tool name formatting."""

    def test_mcp_tool_name_transformed(self) -> None:
        """AC1: MCP tool name is split, last segment extracted, and title-cased."""
        assert format_tool_name("mcp__projects__list_projects") == "List Projects"

    def test_mcp_tool_with_single_word(self) -> None:
        """MCP tool with single-word last segment is title-cased."""
        assert format_tool_name("mcp__memory__search") == "Search"

    def test_mcp_tool_with_many_segments(self) -> None:
        """MCP tool with multiple __ segments takes the last one."""
        assert format_tool_name("mcp__context7__query_docs") == "Query Docs"

    def test_non_mcp_tool_unchanged(self) -> None:
        """AC2: Non-MCP tool names pass through unchanged."""
        assert format_tool_name("CustomMCP") == "CustomMCP"

    def test_known_tool_unchanged(self) -> None:
        """Known tool names like 'Read' pass through unchanged."""
        assert format_tool_name("Read") == "Read"

    def test_empty_string_unchanged(self) -> None:
        """Empty string passes through unchanged."""
        assert format_tool_name("") == ""


class TestSummarizeMcpTool:
    """Tests for MCP tool names in summarize_tool_activity."""

    def test_single_mcp_tool_formatted(self) -> None:
        """AC4: Single MCP tool shows formatted name in summary."""
        activity = ToolActivity(
            tool_name="mcp__projects__list_projects", tool_input={},
        )
        assert summarize_tool_activity([activity]) == "Used List Projects"

    def test_multiple_mcp_tools_aggregated(self) -> None:
        """AC3: 3+ MCP tool uses show formatted name in aggregated summary."""
        activities = [
            ToolActivity(tool_name="mcp__projects__list_projects", tool_input={}),
            ToolActivity(tool_name="mcp__projects__list_projects", tool_input={}),
            ToolActivity(tool_name="mcp__projects__list_projects", tool_input={}),
        ]
        assert summarize_tool_activity(activities) == "Used List Projects 3 times"

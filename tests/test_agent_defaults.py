"""Tests for agent_defaults module."""

import pytest

from tachikoma.agent_defaults import HARDCODED_ENV, merge_env


class TestMergeEnv:
    """Tests for merge_env()."""

    def test_empty_config_returns_hardcoded_defaults(self) -> None:
        result = merge_env({})
        assert result == HARDCODED_ENV

    def test_merges_config_with_defaults(self) -> None:
        result = merge_env({"FOO": "bar"})

        assert result["FOO"] == "bar"
        assert result["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_collision_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="reserved keys"):
            merge_env({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"})

    def test_collision_error_lists_keys(self) -> None:
        with pytest.raises(ValueError, match="CLAUDE_CODE_DISABLE_AUTO_MEMORY"):
            merge_env({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0", "OTHER": "ok"})

    def test_multiple_config_values(self) -> None:
        result = merge_env({"A": "1", "B": "2"})

        assert result["A"] == "1"
        assert result["B"] == "2"
        assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" in result

    def test_auto_injected_included_in_result(self) -> None:
        result = merge_env({}, auto_injected={"TZ": "America/Buenos_Aires"})

        assert result["TZ"] == "America/Buenos_Aires"
        assert result["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_user_override_wins_over_auto_injected(self) -> None:
        result = merge_env(
            {"TZ": "UTC"},
            auto_injected={"TZ": "America/Buenos_Aires"},
        )

        assert result["TZ"] == "UTC"

    def test_auto_injected_no_collision_error(self) -> None:
        # Auto-injected TZ + user TZ should NOT raise (auto-injected is overridable)
        result = merge_env(
            {"TZ": "UTC"},
            auto_injected={"TZ": "America/Buenos_Aires"},
        )

        assert result["TZ"] == "UTC"

    def test_auto_injected_with_hardcoded_key_no_error(self) -> None:
        # Auto-injected key matching a hardcoded key should NOT raise
        # (auto-injected bypasses collision check; hardcoded wins in merge)
        result = merge_env(
            {},
            auto_injected={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"},
        )

        assert result["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_auto_injected_shadowed_by_hardcoded(self) -> None:
        # Hardcoded always wins over auto-injected (defense in depth)
        result = merge_env(
            {},
            auto_injected={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"},
        )

        assert result["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_auto_injected_none_backward_compatible(self) -> None:
        result = merge_env({"FOO": "bar"}, auto_injected=None)

        assert result["FOO"] == "bar"
        assert result["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert "TZ" not in result

"""Tests for memory maintenance tick functions."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.config import MaintenanceSettings
from tachikoma.memory.index import HEAVY_INDEX_REBUILD_PROMPT
from tachikoma.memory.maintenance import (
    CONTEXT_MAINTENANCE_PROMPT,
    EPISODIC_MAINTENANCE_PROMPT,
    FACTS_MAINTENANCE_PROMPT,
    MAINTENANCE_TOOLS,
    PREFERENCES_MAINTENANCE_PROMPT,
    context_maintenance_tick,
    episodic_maintenance_tick,
    facts_maintenance_tick,
    git_commit_context_changes,
    git_commit_memory_changes,
    maintenance_allow_rules,
    preferences_maintenance_tick,
)
from tachikoma.memory.prompts import INDEX_LIGHT_MAINTENANCE_SECTION
from tachikoma.post_processing import MAINTENANCE_BASH_HOOK, abs_rule


@pytest.fixture
def agent_defaults(tmp_path: Path) -> AgentDefaults:
    return AgentDefaults(cwd=tmp_path)


@pytest.fixture
def maintenance_settings() -> MaintenanceSettings:
    return MaintenanceSettings()


class TestMaintenanceAllowRules:
    """Tests for maintenance_allow_rules()."""

    def test_includes_read_glob_grep_bash_unrestricted(self) -> None:
        rules = maintenance_allow_rules(Path("/workspace"))

        assert "Read" in rules
        assert "Glob" in rules
        assert "Grep" in rules
        assert "Bash" in rules

    def test_edit_scoped_to_target(self) -> None:
        scope = Path("/workspace/memories/episodic")
        rules = maintenance_allow_rules(scope)

        assert abs_rule("Edit", scope) in rules

    def test_write_scoped_to_target(self) -> None:
        scope = Path("/workspace/memories/facts")
        rules = maintenance_allow_rules(scope)

        assert abs_rule("Write", scope) in rules


class TestGitCommitMemoryChanges:
    """Tests for git_commit_memory_changes()."""

    async def test_noop_when_no_uncommitted_changes(
        self, agent_defaults: AgentDefaults, mocker: MockerFixture
    ) -> None:
        mock_has_changes = mocker.patch(
            "tachikoma.memory.maintenance.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=False,
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )

        await git_commit_memory_changes(agent_defaults, "episodic")

        mock_has_changes.assert_awaited_once_with(agent_defaults.cwd)
        mock_qac.assert_not_called()

    async def test_calls_query_and_consume_when_changes_exist(
        self, agent_defaults: AgentDefaults, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=True,
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )

        await git_commit_memory_changes(agent_defaults, "episodic")

        mock_qac.assert_awaited_once()
        call_kwargs = mock_qac.call_args
        prompt = call_kwargs[0][0]
        assert "git add memories/episodic/" in prompt
        assert "memory maintenance: episodic" in prompt

    async def test_uses_git_tools_and_hooks(
        self, agent_defaults: AgentDefaults, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=True,
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )

        await git_commit_memory_changes(agent_defaults, "facts")

        call_kwargs = mock_qac.call_args
        assert call_kwargs[1].get("model") == agent_defaults.processor_model


class TestEpisodicMaintenanceTick:
    """Tests for episodic_maintenance_tick()."""

    async def test_calls_query_and_consume_with_correct_tools(
        self,
        agent_defaults: AgentDefaults,
        maintenance_settings: MaintenanceSettings,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await episodic_maintenance_tick(agent_defaults, maintenance_settings)

        mock_qac.assert_awaited_once()
        call_kwargs = mock_qac.call_args
        assert call_kwargs[1]["tools"] == MAINTENANCE_TOOLS
        assert call_kwargs[1]["pre_tool_use_hooks"] == [MAINTENANCE_BASH_HOOK]
        assert call_kwargs[1]["model"] == agent_defaults.processor_model

    async def test_passes_threshold_values_in_prompt(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        settings = MaintenanceSettings(
            recent_days=30, weekly_threshold_months=6, monthly_threshold_months=24
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await episodic_maintenance_tick(agent_defaults, settings)

        prompt = mock_qac.call_args[0][0]
        assert "30" in prompt
        assert "6 months" in prompt
        assert "24 months" in prompt

    async def test_replaces_workspace_placeholder(
        self,
        agent_defaults: AgentDefaults,
        maintenance_settings: MaintenanceSettings,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await episodic_maintenance_tick(agent_defaults, maintenance_settings)

        prompt = mock_qac.call_args[0][0]
        assert "$WORKSPACE" not in prompt
        assert str(agent_defaults.cwd) in prompt

    async def test_uses_scoped_allow_rules(
        self,
        agent_defaults: AgentDefaults,
        maintenance_settings: MaintenanceSettings,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await episodic_maintenance_tick(agent_defaults, maintenance_settings)

        allow = mock_qac.call_args[1]["allow"]
        expected_scope = agent_defaults.cwd / "memories" / "episodic"
        assert abs_rule("Edit", expected_scope) in allow
        assert abs_rule("Write", expected_scope) in allow

    async def test_calls_git_commit_after_agent(
        self,
        agent_defaults: AgentDefaults,
        maintenance_settings: MaintenanceSettings,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mock_commit = mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await episodic_maintenance_tick(agent_defaults, maintenance_settings)

        mock_commit.assert_awaited_once_with(agent_defaults, "episodic")


class TestFactsMaintenanceTick:
    """Tests for facts_maintenance_tick()."""

    async def test_calls_query_and_consume(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        mock_qac.assert_awaited_once()
        call_kwargs = mock_qac.call_args
        assert call_kwargs[1]["tools"] == MAINTENANCE_TOOLS
        assert call_kwargs[1]["pre_tool_use_hooks"] == [MAINTENANCE_BASH_HOOK]
        assert call_kwargs[1]["model"] == agent_defaults.processor_model

    async def test_replaces_workspace_placeholder(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "$WORKSPACE" not in prompt
        assert str(agent_defaults.cwd) in prompt

    async def test_uses_scoped_allow_rules_for_facts(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        allow = mock_qac.call_args[1]["allow"]
        expected_scope = agent_defaults.cwd / "memories" / "facts"
        assert abs_rule("Edit", expected_scope) in allow

    async def test_calls_git_commit_with_facts_type(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mock_commit = mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        mock_commit.assert_awaited_once_with(agent_defaults, "facts")


class TestPreferencesMaintenanceTick:
    """Tests for preferences_maintenance_tick()."""

    async def test_calls_query_and_consume(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        mock_qac.assert_awaited_once()
        call_kwargs = mock_qac.call_args
        assert call_kwargs[1]["tools"] == MAINTENANCE_TOOLS
        assert call_kwargs[1]["pre_tool_use_hooks"] == [MAINTENANCE_BASH_HOOK]

    async def test_replaces_workspace_placeholder(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "$WORKSPACE" not in prompt

    async def test_calls_git_commit_with_preferences_type(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mock_commit = mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        mock_commit.assert_awaited_once_with(agent_defaults, "preferences")


class TestPromptContent:
    """Tests verifying prompt content matches spec requirements."""

    def test_episodic_prompt_includes_tier_strategies(self) -> None:
        assert "Recent" in EPISODIC_MAINTENANCE_PROMPT
        assert "Weekly consolidation" in EPISODIC_MAINTENANCE_PROMPT
        assert "Monthly consolidation" in EPISODIC_MAINTENANCE_PROMPT
        assert "Delete" in EPISODIC_MAINTENANCE_PROMPT

    def test_episodic_prompt_protects_recent_files(self) -> None:
        assert "NEVER delete files or remove substantive content" in EPISODIC_MAINTENANCE_PROMPT

    def test_facts_prompt_includes_evaluation_criteria(self) -> None:
        assert "Staleness" in FACTS_MAINTENANCE_PROMPT
        assert "Redundancy" in FACTS_MAINTENANCE_PROMPT
        assert "Overlap" in FACTS_MAINTENANCE_PROMPT

    def test_preferences_prompt_includes_evaluation_criteria(self) -> None:
        assert "Redundancy" in PREFERENCES_MAINTENANCE_PROMPT
        assert "Overlap" in PREFERENCES_MAINTENANCE_PROMPT

    def test_all_prompts_include_empty_store_noop(self) -> None:
        prompts = [
            EPISODIC_MAINTENANCE_PROMPT,
            FACTS_MAINTENANCE_PROMPT,
            PREFERENCES_MAINTENANCE_PROMPT,
            CONTEXT_MAINTENANCE_PROMPT,
        ]
        for prompt in prompts:
            lower = prompt.lower()
            assert "empty" in lower or "no" in lower or "does not exist" in lower

    def test_all_prompts_include_malformed_file_skip(self) -> None:
        prompts = [
            EPISODIC_MAINTENANCE_PROMPT,
            FACTS_MAINTENANCE_PROMPT,
            PREFERENCES_MAINTENANCE_PROMPT,
        ]
        for prompt in prompts:
            assert "malformed" in prompt.lower()

    def test_all_prompts_include_idempotency(self) -> None:
        prompts = [
            EPISODIC_MAINTENANCE_PROMPT,
            FACTS_MAINTENANCE_PROMPT,
            PREFERENCES_MAINTENANCE_PROMPT,
            CONTEXT_MAINTENANCE_PROMPT,
        ]
        for prompt in prompts:
            assert "Idempotency" in prompt

    def test_preferences_prompt_includes_misclassification_detection(self) -> None:
        """AC: Preferences maintenance includes misclassification detection."""
        assert "Misclassification" in PREFERENCES_MAINTENANCE_PROMPT
        assert "subjective choice" in PREFERENCES_MAINTENANCE_PROMPT.lower()

    def test_preferences_prompt_includes_size_enforcement(self) -> None:
        """AC: Preferences maintenance enforces 30-line file size limit."""
        assert "30 lines" in PREFERENCES_MAINTENANCE_PROMPT

    def test_preferences_prompt_includes_cross_store_facts_overlap(self) -> None:
        """AC: Preferences maintenance checks facts store for overlap."""
        assert "memories/facts/" in PREFERENCES_MAINTENANCE_PROMPT

    def test_facts_prompt_includes_size_enforcement(self) -> None:
        """AC: Facts maintenance enforces 40-line file size limit."""
        assert "40 lines" in FACTS_MAINTENANCE_PROMPT

    def test_facts_prompt_includes_context_file_overlap(self) -> None:
        """AC: Facts maintenance checks context files for overlap."""
        assert "context/" in FACTS_MAINTENANCE_PROMPT
        assert "USER.md" in FACTS_MAINTENANCE_PROMPT or "AGENTS.md" in FACTS_MAINTENANCE_PROMPT


class TestGitCommitContextChanges:
    """Tests for git_commit_context_changes()."""

    async def test_noop_when_no_uncommitted_changes(
        self, agent_defaults: AgentDefaults, mocker: MockerFixture
    ) -> None:
        mock_has_changes = mocker.patch(
            "tachikoma.memory.maintenance.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=False,
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )

        await git_commit_context_changes(agent_defaults)

        mock_has_changes.assert_awaited_once_with(agent_defaults.cwd)
        mock_qac.assert_not_called()

    async def test_calls_query_and_consume_when_changes_exist(
        self, agent_defaults: AgentDefaults, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=True,
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )

        await git_commit_context_changes(agent_defaults)

        mock_qac.assert_awaited_once()
        prompt = mock_qac.call_args[0][0]
        assert "git add context/" in prompt
        assert "memory maintenance: context" in prompt


class TestContextMaintenanceTick:
    """Tests for context_maintenance_tick()."""

    async def test_calls_query_and_consume_with_correct_tools(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_context_changes",
            new_callable=AsyncMock,
        )

        await context_maintenance_tick(agent_defaults)

        mock_qac.assert_awaited_once()
        call_kwargs = mock_qac.call_args
        assert call_kwargs[1]["tools"] == MAINTENANCE_TOOLS
        assert call_kwargs[1]["pre_tool_use_hooks"] == [MAINTENANCE_BASH_HOOK]
        assert call_kwargs[1]["model"] == agent_defaults.processor_model

    async def test_replaces_workspace_placeholder(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_context_changes",
            new_callable=AsyncMock,
        )

        await context_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "$WORKSPACE" not in prompt
        assert str(agent_defaults.cwd) in prompt

    async def test_uses_scoped_allow_rules_for_context(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_context_changes",
            new_callable=AsyncMock,
        )

        await context_maintenance_tick(agent_defaults)

        allow = mock_qac.call_args[1]["allow"]
        expected_scope = agent_defaults.cwd / "context"
        assert abs_rule("Edit", expected_scope) in allow
        assert abs_rule("Write", expected_scope) in allow

    async def test_calls_git_commit_after_agent(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mock_commit = mocker.patch(
            "tachikoma.memory.maintenance.git_commit_context_changes",
            new_callable=AsyncMock,
        )

        await context_maintenance_tick(agent_defaults)

        mock_commit.assert_awaited_once_with(agent_defaults)


class TestContextPromptContent:
    """Tests verifying context maintenance prompt content."""

    def test_includes_evaluation_criteria(self) -> None:
        assert "Staleness" in CONTEXT_MAINTENANCE_PROMPT
        assert "Redundancy" in CONTEXT_MAINTENANCE_PROMPT
        assert "Overlap" in CONTEXT_MAINTENANCE_PROMPT

    def test_includes_size_limits(self) -> None:
        assert "120 lines" in CONTEXT_MAINTENANCE_PROMPT
        assert "400 lines" in CONTEXT_MAINTENANCE_PROMPT

    def test_includes_cleanup_only_constraint(self) -> None:
        assert "Cleanup-only" in CONTEXT_MAINTENANCE_PROMPT
        assert "Do NOT add new content" in CONTEXT_MAINTENANCE_PROMPT

    def test_includes_three_context_files(self) -> None:
        assert "SOUL.md" in CONTEXT_MAINTENANCE_PROMPT
        assert "USER.md" in CONTEXT_MAINTENANCE_PROMPT
        assert "AGENTS.md" in CONTEXT_MAINTENANCE_PROMPT

    def test_includes_conservative_guard(self) -> None:
        assert "Conservative" in CONTEXT_MAINTENANCE_PROMPT
        assert "vague hints" in CONTEXT_MAINTENANCE_PROMPT


class TestFactsDayOfWeekDispatch:
    """Tests for day-of-week dispatch in facts_maintenance_tick."""

    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5])
    async def test_weekday_appends_light_maintenance_section(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
        weekday: int,
    ) -> None:
        """Mon–Sat: INDEX_LIGHT_MAINTENANCE_SECTION appended to prompt."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": weekday},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "Memory Index Consistency" in prompt

    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5])
    async def test_weekday_uses_standard_tools(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
        weekday: int,
    ) -> None:
        """Mon–Sat: standard MAINTENANCE_TOOLS, no Agent."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": weekday},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        tools = mock_qac.call_args[1]["tools"]
        assert tools == MAINTENANCE_TOOLS
        assert "Agent" not in tools

    async def test_sunday_appends_heavy_rebuild_prompt(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: HEAVY_INDEX_REBUILD_PROMPT appended to prompt."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "index rebuild orchestrator" in prompt

    async def test_sunday_includes_agent_in_tools(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: Agent tool added alongside MAINTENANCE_TOOLS."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        tools = mock_qac.call_args[1]["tools"]
        assert "Agent" in tools
        for tool in MAINTENANCE_TOOLS:
            assert tool in tools

    async def test_sunday_uses_extraction_allow_rules_with_agent(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: allow rules include Agent permission."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        allow = mock_qac.call_args[1]["allow"]
        assert "Agent" in allow

    async def test_sunday_replaces_type_placeholder_in_prompt(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: <type> placeholder replaced with 'facts' in prompt."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await facts_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        # The <type> placeholder should be replaced; check for facts-specific path
        assert "memories/facts/" in prompt
        assert "<type>" not in prompt


class TestPreferencesDayOfWeekDispatch:
    """Tests for day-of-week dispatch in preferences_maintenance_tick."""

    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5])
    async def test_weekday_appends_light_maintenance_section(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
        weekday: int,
    ) -> None:
        """Mon–Sat: INDEX_LIGHT_MAINTENANCE_SECTION appended to prompt."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": weekday},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "Memory Index Consistency" in prompt

    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5])
    async def test_weekday_uses_standard_tools(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
        weekday: int,
    ) -> None:
        """Mon–Sat: standard MAINTENANCE_TOOLS, no Agent."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": weekday},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        tools = mock_qac.call_args[1]["tools"]
        assert tools == MAINTENANCE_TOOLS
        assert "Agent" not in tools

    async def test_sunday_appends_heavy_rebuild_prompt(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: HEAVY_INDEX_REBUILD_PROMPT appended to prompt."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "index rebuild orchestrator" in prompt

    async def test_sunday_includes_agent_in_tools(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: Agent tool added alongside MAINTENANCE_TOOLS."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        tools = mock_qac.call_args[1]["tools"]
        assert "Agent" in tools

    async def test_sunday_replaces_type_placeholder_in_prompt(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Sunday: <type> placeholder replaced with 'preferences' in prompt."""
        mocker.patch(
            "tachikoma.memory.maintenance.datetime",
            **{"datetime.now.return_value.weekday.return_value": 6},
        )
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        await preferences_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "memories/preferences/" in prompt
        assert "<type>" not in prompt


class TestEpisodicAndContextUnchanged:
    """Verify episodic and context ticks have no day-of-week logic."""

    async def test_episodic_tick_no_index_section(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Episodic tick should NOT include index maintenance sections."""
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_memory_changes",
            new_callable=AsyncMock,
        )

        settings = MaintenanceSettings()
        await episodic_maintenance_tick(agent_defaults, settings)

        prompt = mock_qac.call_args[0][0]
        assert "Memory Index Consistency" not in prompt
        assert "index rebuild orchestrator" not in prompt

    async def test_context_tick_no_index_section(
        self,
        agent_defaults: AgentDefaults,
        mocker: MockerFixture,
    ) -> None:
        """Context tick should NOT include index maintenance sections."""
        mock_qac = mocker.patch(
            "tachikoma.memory.maintenance.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.memory.maintenance.git_commit_context_changes",
            new_callable=AsyncMock,
        )

        await context_maintenance_tick(agent_defaults)

        prompt = mock_qac.call_args[0][0]
        assert "Memory Index Consistency" not in prompt
        assert "index rebuild orchestrator" not in prompt


class TestIndexPromptContent:
    """Tests verifying index-related prompt content for R2/R6/R7 coverage."""

    def test_light_maintenance_section_includes_consistency_verification(self) -> None:
        assert "Glob" in INDEX_LIGHT_MAINTENANCE_SECTION
        assert "MEMORY.md" in INDEX_LIGHT_MAINTENANCE_SECTION

    def test_light_maintenance_section_includes_placeholder_format(self) -> None:
        assert "Description pending update" in INDEX_LIGHT_MAINTENANCE_SECTION

    def test_light_maintenance_section_does_not_regenerate(self) -> None:
        assert "Do NOT regenerate" in INDEX_LIGHT_MAINTENANCE_SECTION

    def test_light_maintenance_section_removes_stale_entries(self) -> None:
        assert "no longer exist" in INDEX_LIGHT_MAINTENANCE_SECTION

    def test_light_maintenance_section_includes_r2_coverage(self) -> None:
        """R2: Light section instructs agent to update MEMORY.md on file ops."""
        assert "create, modify, or delete" in INDEX_LIGHT_MAINTENANCE_SECTION.lower()
        assert "MEMORY.md" in INDEX_LIGHT_MAINTENANCE_SECTION

    def test_heavy_rebuild_includes_r2_coverage(self) -> None:
        """R2: Heavy rebuild prompt instructs agent to write MEMORY.md."""
        assert "MEMORY.md" in HEAVY_INDEX_REBUILD_PROMPT
        assert "merge" in HEAVY_INDEX_REBUILD_PROMPT.lower()
        assert "rename" in HEAVY_INDEX_REBUILD_PROMPT.lower()

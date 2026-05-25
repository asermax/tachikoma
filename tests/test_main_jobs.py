"""Tests for build_scheduler_jobs priority assignment."""

from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from tachikoma.__main__ import build_scheduler_jobs
from tachikoma.config import (
    MaintenanceSettings,
    MemorySettings,
    SchedulerSettings,
    Settings,
    UpdatesSettings,
)


def _make_settings(
    *,
    maintenance_enabled: bool = True,
    updates_enabled: bool = True,
) -> Settings:
    return Settings(
        memory=MemorySettings(
            maintenance=MaintenanceSettings(enabled=maintenance_enabled),
        ),
        updates=UpdatesSettings(enabled=updates_enabled),
        scheduler=SchedulerSettings(),
    )


def _build_jobs(settings: Settings) -> list:
    return build_scheduler_jobs(
        settings=settings,
        tz=ZoneInfo("UTC"),
        task_repository=MagicMock(),
        buffer=MagicMock(),
        background_runner=MagicMock(),
        bus=MagicMock(),
        agent_defaults=MagicMock(),
        skill_registry=MagicMock(),
        app_state_repo=MagicMock(),
        plugin_manager=MagicMock(),
        plugin_state_repo=MagicMock(),
    )


class TestMainPriorityRegistration:
    """Housekeeping and maintenance jobs are low; dispatch-driving jobs are high."""

    def test_default_settings_priority_assignment(self) -> None:
        jobs = _build_jobs(_make_settings())
        low_names = [j.name for j in jobs if j.priority == "low"]
        high_names = [j.name for j in jobs if j.priority == "high"]

        assert sorted(low_names) == [
            "context_maintenance",
            "episodic_maintenance",
            "facts_maintenance",
            "one_shot_cleanup",
            "plugin_update_check",
            "preferences_maintenance",
            "update_checker",
        ]
        assert all(j.priority == "high" for j in jobs if j.name not in low_names)
        assert "instance_generator" in high_names
        assert "session_task_scheduler" in high_names

    def test_maintenance_disabled_omits_maintenance_low_jobs(self) -> None:
        jobs = _build_jobs(_make_settings(maintenance_enabled=False))
        low_names = [j.name for j in jobs if j.priority == "low"]
        assert sorted(low_names) == [
            "one_shot_cleanup",
            "plugin_update_check",
            "update_checker",
        ]

    def test_maintenance_and_updates_disabled(self) -> None:
        jobs = _build_jobs(_make_settings(maintenance_enabled=False, updates_enabled=False))
        low_names = [j.name for j in jobs if j.priority == "low"]
        assert sorted(low_names) == [
            "one_shot_cleanup",
            "plugin_update_check",
        ]

    def test_maintenance_dispatch_order_is_lightest_to_heaviest(self) -> None:
        jobs = _build_jobs(_make_settings())
        low_jobs = [j for j in jobs if j.priority == "low"]
        assert [j.name for j in low_jobs] == [
            "one_shot_cleanup",
            "update_checker",
            "plugin_update_check",
            "context_maintenance",
            "preferences_maintenance",
            "episodic_maintenance",
            "facts_maintenance",
        ]

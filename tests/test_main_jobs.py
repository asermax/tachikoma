"""Tests for build_scheduler_jobs priority assignment (AC10)."""

from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from tachikoma.__main__ import build_scheduler_jobs
from tachikoma.config import MaintenanceSettings, MemorySettings, SchedulerSettings, Settings


def _make_settings(*, maintenance_enabled: bool = True) -> Settings:
    return Settings(
        memory=MemorySettings(
            maintenance=MaintenanceSettings(enabled=maintenance_enabled),
        ),
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
    """AC10: Exactly four maintenance jobs are low; everything else is high."""

    def test_main_priority_registration(self) -> None:
        jobs = _build_jobs(_make_settings())
        low_names = [j.name for j in jobs if j.priority == "low"]
        high_names = [j.name for j in jobs if j.priority == "high"]

        assert sorted(low_names) == [
            "context_maintenance",
            "episodic_maintenance",
            "facts_maintenance",
            "preferences_maintenance",
        ]
        assert all(j.priority == "high" for j in jobs if j.name not in low_names)
        assert "instance_generator" in high_names
        assert "session_task_scheduler" in high_names

    def test_maintenance_disabled_omits_low_priority_jobs(self) -> None:
        jobs = _build_jobs(_make_settings(maintenance_enabled=False))
        low_names = [j.name for j in jobs if j.priority == "low"]
        assert low_names == []

    def test_maintenance_dispatch_order_is_lightest_to_heaviest(self) -> None:
        jobs = _build_jobs(_make_settings())
        low_jobs = [j for j in jobs if j.priority == "low"]
        assert [j.name for j in low_jobs] == [
            "context_maintenance",
            "preferences_maintenance",
            "episodic_maintenance",
            "facts_maintenance",
        ]

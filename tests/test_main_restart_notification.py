"""Tests for the back-online session-task scheduling on startup (DES-011)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tachikoma.__main__ import (
    _build_back_online_prompt,
    _consume_restart_notification,
)
from tachikoma.tasks.model import TaskDefinition
from tachikoma.updates.rollback import RestartNotification


def _make_notification(
    reason: str = "manual",
    previous_version: str | None = None,
    new_version: str | None = None,
) -> RestartNotification:
    return RestartNotification(
        reason=reason,  # type: ignore[arg-type]
        previous_version=previous_version,
        new_version=new_version,
        timestamp="2026-04-29T00:00:00+00:00",
    )


class TestBuildBackOnlinePrompt:
    def test_manual_prompt_omits_version_clause(self) -> None:
        prompt = _build_back_online_prompt(_make_notification(reason="manual"))

        assert "manual restart" in prompt
        assert "upgraded" not in prompt

    def test_update_prompt_includes_version_transition(self) -> None:
        prompt = _build_back_online_prompt(
            _make_notification(
                reason="update",
                previous_version="1.55.0",
                new_version="1.56.0",
            )
        )

        assert "update restart" in prompt
        assert "upgraded from 1.55.0 to 1.56.0" in prompt

    def test_update_prompt_without_versions_falls_back_to_no_clause(self) -> None:
        prompt = _build_back_online_prompt(
            _make_notification(reason="update", previous_version=None, new_version=None)
        )

        assert "update restart" in prompt
        assert "upgraded" not in prompt


class TestConsumeRestartNotification:
    @pytest.mark.asyncio
    async def test_rollback_dispatched_clears_and_skips(self, monkeypatch) -> None:
        clear_calls: list[None] = []
        monkeypatch.setattr(
            "tachikoma.__main__.clear_restart_notification",
            lambda: clear_calls.append(None),
        )
        repo = AsyncMock()

        await _consume_restart_notification(
            repo,
            notification=_make_notification(),
            rollback_was_dispatched=True,
        )

        assert clear_calls == [None]
        repo.create_definition.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_notification_clears_and_skips(self, monkeypatch) -> None:
        clear_calls: list[None] = []
        monkeypatch.setattr(
            "tachikoma.__main__.clear_restart_notification",
            lambda: clear_calls.append(None),
        )
        repo = AsyncMock()

        await _consume_restart_notification(
            repo,
            notification=None,
            rollback_was_dispatched=False,
        )

        assert clear_calls == [None]
        repo.create_definition.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_path_persists_session_task(self, monkeypatch) -> None:
        clear_calls: list[None] = []
        monkeypatch.setattr(
            "tachikoma.__main__.clear_restart_notification",
            lambda: clear_calls.append(None),
        )
        repo = AsyncMock()

        before = datetime.now(UTC)
        await _consume_restart_notification(
            repo,
            notification=_make_notification(reason="manual"),
            rollback_was_dispatched=False,
        )
        after = datetime.now(UTC)

        assert clear_calls == [None]
        repo.create_definition.assert_awaited_once()
        definition: TaskDefinition = repo.create_definition.await_args.args[0]
        assert definition.task_type == "session"
        assert definition.name == "Back online"
        assert definition.schedule.type == "once"
        assert definition.schedule.at is not None
        assert before + timedelta(seconds=25) <= definition.schedule.at
        assert definition.schedule.at <= after + timedelta(seconds=35)
        assert "manual restart" in definition.prompt
        assert "upgraded" not in definition.prompt

    @pytest.mark.asyncio
    async def test_update_path_carries_version_transition_into_prompt(self, monkeypatch) -> None:
        monkeypatch.setattr("tachikoma.__main__.clear_restart_notification", lambda: None)
        repo = AsyncMock()

        await _consume_restart_notification(
            repo,
            notification=_make_notification(
                reason="update",
                previous_version="1.55.0",
                new_version="1.56.0",
            ),
            rollback_was_dispatched=False,
        )

        repo.create_definition.assert_awaited_once()
        definition: TaskDefinition = repo.create_definition.await_args.args[0]
        assert "upgraded from 1.55.0 to 1.56.0" in definition.prompt

    @pytest.mark.asyncio
    async def test_consume_once_clear_happens_before_persist(self, monkeypatch) -> None:
        """If create_definition raises, the marker must already be cleared."""
        call_order: list[str] = []

        def record_clear() -> None:
            call_order.append("clear")

        async def record_create_definition_and_fail(*args: Any, **kwargs: Any) -> None:
            call_order.append("create_definition")
            raise RuntimeError("boom")

        monkeypatch.setattr("tachikoma.__main__.clear_restart_notification", record_clear)
        repo = AsyncMock()
        repo.create_definition = AsyncMock(side_effect=record_create_definition_and_fail)

        await _consume_restart_notification(
            repo,
            notification=_make_notification(reason="manual"),
            rollback_was_dispatched=False,
        )

        assert call_order == ["clear", "create_definition"]

"""Tests for skills filesystem watcher.

Tests for DLT-038: Hot-reload skills at runtime.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.skills.events import SkillsChanged
from tachikoma.skills.registry import SkillRegistry
from tachikoma.skills.watcher import watch_skills


class TestWatchSkills:
    """Tests for watch_skills async function."""

    async def test_mark_dirty_called_on_changes(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """AC: mark_dirty() is called when watcher yields changes."""
        # Create skills directory
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # Create mock registry and bus
        registry = MagicMock(spec=SkillRegistry)
        registry.mark_dirty = MagicMock()
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        # Create mock awatch that yields one change set then raises CancelledError
        async def mock_awatch_generator(*args, **kwargs):
            # Yield a change set (set of tuples: (Change enum, path string))
            yield {("added", str(skills_dir / "test.md"))}
            # Simulate cancellation after first yield
            raise asyncio.CancelledError()

        mocker.patch(
            "tachikoma.skills.watcher.awatch",
            side_effect=mock_awatch_generator,
        )

        # Run watcher with timeout to prevent hanging
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(
                watch_skills(skills_dir, registry, bus),
                timeout=1.0,
            )

        # Verify mark_dirty was called
        registry.mark_dirty.assert_called_once()

    async def test_dispatches_skills_changed_event(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """AC: SkillsChanged event is dispatched on the bus."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        registry = MagicMock(spec=SkillRegistry)
        registry.mark_dirty = MagicMock()
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        async def mock_awatch_generator(*args, **kwargs):
            yield {("modified", str(skills_dir / "test.md"))}
            raise asyncio.CancelledError()

        mocker.patch("tachikoma.skills.watcher.awatch", side_effect=mock_awatch_generator)

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(
                watch_skills(skills_dir, registry, bus),
                timeout=1.0,
            )

        # Verify dispatch was called with SkillsChanged event
        bus.dispatch.assert_called_once()
        call_args = bus.dispatch.call_args[0]
        assert isinstance(call_args[0], SkillsChanged)

    async def test_exception_caught_and_logged(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """AC: Exceptions from awatch are caught and logged (task doesn't crash)."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        registry = MagicMock(spec=SkillRegistry)
        registry.mark_dirty = MagicMock()
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        # Mock awatch to raise a non-CancelledError exception immediately
        async def mock_awatch_generator(*args, **kwargs):
            raise OSError("inotify watch limit exhausted")
            yield  # Never reached, but makes it a generator

        mocker.patch("tachikoma.skills.watcher.awatch", side_effect=mock_awatch_generator)

        # Should NOT raise — watcher catches and logs
        await watch_skills(skills_dir, registry, bus)

        # Registry should NOT have been marked dirty
        registry.mark_dirty.assert_not_called()

    async def test_cancelled_error_propagates(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """AC: CancelledError propagates (not caught by Exception handler)."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        registry = MagicMock(spec=SkillRegistry)
        registry.mark_dirty = MagicMock()
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        async def mock_awatch_generator(*args, **kwargs):
            raise asyncio.CancelledError()
            yield  # Never reached

        mocker.patch("tachikoma.skills.watcher.awatch", side_effect=mock_awatch_generator)

        # CancelledError should propagate
        with pytest.raises(asyncio.CancelledError):
            await watch_skills(skills_dir, registry, bus)

    async def test_missing_directory_logs_and_returns(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """AC: Missing directory logs and returns gracefully."""
        non_existent = tmp_path / "does-not-exist"

        registry = MagicMock(spec=SkillRegistry)
        registry.mark_dirty = MagicMock()
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        # Should return without error
        await watch_skills(non_existent, registry, bus)

        # awatch should never be called
        registry.mark_dirty.assert_not_called()
        bus.dispatch.assert_not_called()

    async def test_debounce_passed_to_awatch(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """AC: debounce=5000 and rust_timeout=500 passed to awatch."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        registry = MagicMock(spec=SkillRegistry)
        registry.mark_dirty = MagicMock()
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        # Track actual call kwargs
        call_kwargs = {}

        async def mock_awatch_generator(*args, **kwargs):
            nonlocal call_kwargs
            call_kwargs = kwargs
            raise asyncio.CancelledError()
            yield  # Never reached

        mocker.patch("tachikoma.skills.watcher.awatch", side_effect=mock_awatch_generator)

        with pytest.raises(asyncio.CancelledError):
            await watch_skills(skills_dir, registry, bus)

        # Verify awatch was called with correct parameters
        assert call_kwargs.get("debounce") == 5000
        assert call_kwargs.get("rust_timeout") == 2000

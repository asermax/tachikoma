"""Tests for PluginState domain model, ORM model, and PluginStateRepository."""

from datetime import UTC, datetime

import pytest

from tachikoma.database import Database
from tachikoma.plugins.state import PluginState, PluginStateRepository


@pytest.fixture
async def state_repo(tmp_path) -> PluginStateRepository:
    """Initialized PluginStateRepository backed by a temp SQLite file."""
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield PluginStateRepository(database.session_factory)
    await database.close()


def _make_state(**overrides) -> PluginState:
    """Create a PluginState with sensible defaults for testing."""
    defaults = {
        "alias": "test-plugin",
        "installed_version": "abc123def456" * 4 + "abc1",  # 40-char SHA
        "update_status": "unknown",
        "available_version": None,
        "last_checked_at": None,
        "diagnostic": None,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return PluginState(**defaults)


class TestPluginStateModel:
    def test_frozen_dataclass(self) -> None:
        state = _make_state()
        with pytest.raises(AttributeError):
            state.alias = "changed"  # type: ignore[misc]

    def test_fields_preserved(self) -> None:
        now = datetime.now(UTC)
        state = _make_state(
            alias="my-plugin",
            installed_version="a" * 40,
            update_status="up-to-date",
            available_version="b" * 40,
            last_checked_at=now,
            diagnostic="some info",
        )
        assert state.alias == "my-plugin"
        assert state.installed_version == "a" * 40
        assert state.update_status == "up-to-date"
        assert state.available_version == "b" * 40
        assert state.last_checked_at == now
        assert state.diagnostic == "some info"


class TestPluginStateRepositoryCreate:
    async def test_upsert_creates_new_record(self, state_repo: PluginStateRepository) -> None:
        state = _make_state()
        result = await state_repo.upsert(state)

        assert result is not None
        assert result.alias == "test-plugin"
        assert result.installed_version == state.installed_version
        assert result.update_status == "unknown"
        assert result.available_version is None
        assert result.created_at is not None

    async def test_upsert_with_null_version(self, state_repo: PluginStateRepository) -> None:
        state = _make_state(installed_version=None)
        result = await state_repo.upsert(state)

        assert result.installed_version is None

    async def test_upsert_returns_persisted_state(self, state_repo: PluginStateRepository) -> None:
        state = _make_state()
        result = await state_repo.upsert(state)

        # Verify it's a PluginState domain object with a real created_at
        assert isinstance(result, PluginState)
        assert result.created_at is not None


class TestPluginStateRepositoryRead:
    async def test_get_returns_none_for_missing_alias(
        self, state_repo: PluginStateRepository
    ) -> None:
        result = await state_repo.get("nonexistent")
        assert result is None

    async def test_get_returns_persisted_state(self, state_repo: PluginStateRepository) -> None:
        state = _make_state()
        await state_repo.upsert(state)

        result = await state_repo.get("test-plugin")
        assert result is not None
        assert result.alias == "test-plugin"
        assert result.installed_version == state.installed_version

    async def test_get_preserves_all_fields(self, state_repo: PluginStateRepository) -> None:
        now = datetime.now(UTC)
        state = _make_state(
            installed_version="a" * 40,
            update_status="update-available",
            available_version="b" * 40,
            last_checked_at=now,
            diagnostic="check failed",
        )
        await state_repo.upsert(state)

        result = await state_repo.get("test-plugin")
        assert result is not None
        assert result.update_status == "update-available"
        assert result.available_version == "b" * 40
        assert result.last_checked_at is not None
        assert result.diagnostic == "check failed"


class TestPluginStateRepositoryUpdate:
    async def test_upsert_updates_existing_record(self, state_repo: PluginStateRepository) -> None:
        state = _make_state(update_status="unknown")
        await state_repo.upsert(state)

        updated = _make_state(
            update_status="up-to-date",
            installed_version="b" * 40,
        )
        result = await state_repo.upsert(updated)

        assert result.update_status == "up-to-date"
        assert result.installed_version == "b" * 40

    async def test_upsert_idempotent(self, state_repo: PluginStateRepository) -> None:
        state = _make_state()
        await state_repo.upsert(state)
        await state_repo.upsert(state)

        # Should still have exactly one record
        result = await state_repo.get("test-plugin")
        assert result is not None
        assert result.alias == "test-plugin"

    async def test_upsert_clears_optional_fields(self, state_repo: PluginStateRepository) -> None:
        state = _make_state(
            available_version="b" * 40,
            diagnostic="some error",
        )
        await state_repo.upsert(state)

        updated = _make_state(
            available_version=None,
            diagnostic=None,
        )
        result = await state_repo.upsert(updated)

        assert result.available_version is None
        assert result.diagnostic is None


class TestPluginStateRepositoryRemove:
    async def test_remove_deletes_record(self, state_repo: PluginStateRepository) -> None:
        state = _make_state()
        await state_repo.upsert(state)

        await state_repo.remove("test-plugin")
        result = await state_repo.get("test-plugin")
        assert result is None

    async def test_remove_nonexistent_is_noop(self, state_repo: PluginStateRepository) -> None:
        # Should not raise
        await state_repo.remove("nonexistent")

    async def test_does_not_affect_other_records(self, state_repo: PluginStateRepository) -> None:
        state_a = _make_state(alias="plugin-a")
        state_b = _make_state(alias="plugin-b")
        await state_repo.upsert(state_a)
        await state_repo.upsert(state_b)

        await state_repo.remove("plugin-a")

        assert await state_repo.get("plugin-a") is None
        assert await state_repo.get("plugin-b") is not None

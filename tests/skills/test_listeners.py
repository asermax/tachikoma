"""Tests for the plugin-event listener in the skills module.

Covers all three event handlers: PluginInstalled, PluginRemoving, PluginRemoved.
Uses a real EventBus and real SkillRegistry where practical, mocking only the
session registry and file I/O.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bubus import EventBus

from tachikoma.plugins.events import PluginInstalled, PluginRemoved, PluginRemoving
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.sources import LocalPluginSource
from tachikoma.sessions.model import SessionContextEntry
from tachikoma.skills.events import SkillsChanged
from tachikoma.skills.listeners import register_plugin_event_listeners
from tachikoma.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(alias: str, skill_dirs: list[Path] | None = None) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=LocalPluginSource(path=Path("/tmp/dummy")),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test plugin",
            source_format="tachikoma",
            skill_dirs=skill_dirs or [],
        ),
        status="loaded",
        diagnostic=None,
        plugin_dir=Path(f"/tmp/plugins/{alias}"),
    )


def _make_session_entry(
    entry_id: int,
    session_id: str,
    skill_name: str,
    content: str = "skill body",
) -> SessionContextEntry:
    return SessionContextEntry(
        id=entry_id,
        session_id=session_id,
        owner="skills",
        content=content,
        metadata={"skill_name": skill_name},
    )


async def _dispatch_and_cleanup(bus: EventBus, event: object) -> None:
    """Dispatch event, wait for handlers, then stop the bus."""
    await bus.dispatch(event)
    await bus.wait_until_idle()
    await bus.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnPluginInstalled:
    """PluginInstalled → add_namespaced_source + SkillsChanged."""

    async def test_registers_skills_and_dispatches_skills_changed(self, tmp_path: Path) -> None:
        """On PluginInstalled → registry receives add_namespaced_source per skill dir;
        contributed_skills populated; SkillsChanged dispatched."""
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = MagicMock()

        skills_changed_count = 0

        async def on_skills_changed(event: SkillsChanged) -> None:
            nonlocal skills_changed_count
            skills_changed_count += 1

        bus.on(SkillsChanged, on_skills_changed)

        register_plugin_event_listeners(bus, registry, session_registry)

        skill_dir = tmp_path / "skills" / "linter"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: A linter\n---\nLinter skill body"
        )

        plugin = _make_plugin("code-review", skill_dirs=[tmp_path / "skills"])

        await _dispatch_and_cleanup(
            bus, PluginInstalled(alias="code-review", plugin=plugin)
        )

        assert "code-review:linter" in registry.skills
        assert len(plugin.contributed_skills) == 1
        assert plugin.contributed_skills[0].qualified_name == "code-review:linter"
        assert skills_changed_count == 1


class TestOnPluginRemoving:
    """PluginRemoving → mark active session entries deleted."""

    async def test_marks_matching_entries(self, tmp_path: Path) -> None:
        """Entries with matching skill_name get notice prepended + metadata["deleted"]=True."""
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = AsyncMock()

        entry = _make_session_entry(1, "sess-1", "code-review:linter")
        session_registry.get_active_session.return_value = MagicMock(id="sess-1")
        session_registry.find_context_entries_by_skill_name.return_value = [entry]
        session_registry.update_context_entry.return_value = MagicMock()

        register_plugin_event_listeners(bus, registry, session_registry)

        await _dispatch_and_cleanup(
            bus,
            PluginRemoving(
                alias="code-review",
                namespaced_skill_names=["code-review:linter"],
            ),
        )

        # update_context_entry called with updated content and metadata.
        session_registry.update_context_entry.assert_called_once()
        call_kwargs = session_registry.update_context_entry.call_args
        assert call_kwargs.kwargs["content"].startswith("**[Plugin removed:")
        assert call_kwargs.kwargs["metadata"]["deleted"] is True
        assert call_kwargs.kwargs["metadata"]["skill_name"] == "code-review:linter"

    async def test_no_active_session(self, tmp_path: Path) -> None:
        """No active session → no error, no entries touched."""
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = AsyncMock()
        session_registry.get_active_session.return_value = None

        register_plugin_event_listeners(bus, registry, session_registry)

        await _dispatch_and_cleanup(
            bus,
            PluginRemoving(
                alias="code-review",
                namespaced_skill_names=["code-review:linter"],
            ),
        )

        session_registry.find_context_entries_by_skill_name.assert_not_called()

    async def test_entry_vanished_mid_handler(self, tmp_path: Path) -> None:
        """update_context_entry returns None (entry gone) → no error."""
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = AsyncMock()

        entry = _make_session_entry(1, "sess-1", "code-review:linter")
        session_registry.get_active_session.return_value = MagicMock(id="sess-1")
        session_registry.find_context_entries_by_skill_name.return_value = [entry]
        session_registry.update_context_entry.return_value = None  # Entry vanished.

        register_plugin_event_listeners(bus, registry, session_registry)

        # Should not raise.
        await _dispatch_and_cleanup(
            bus,
            PluginRemoving(
                alias="code-review",
                namespaced_skill_names=["code-review:linter"],
            ),
        )

    async def test_preserves_other_metadata(self, tmp_path: Path) -> None:
        """Other metadata keys are preserved when setting deleted=True."""
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = AsyncMock()

        entry = SessionContextEntry(
            id=1,
            session_id="sess-1",
            owner="skills",
            content="body",
            metadata={"skill_name": "cr:linter", "priority": "high"},
        )
        session_registry.get_active_session.return_value = MagicMock(id="sess-1")
        session_registry.find_context_entries_by_skill_name.return_value = [entry]
        session_registry.update_context_entry.return_value = MagicMock()

        register_plugin_event_listeners(bus, registry, session_registry)

        await _dispatch_and_cleanup(
            bus, PluginRemoving(alias="cr", namespaced_skill_names=["cr:linter"])
        )

        call_kwargs = session_registry.update_context_entry.call_args.kwargs
        assert call_kwargs["metadata"]["priority"] == "high"
        assert call_kwargs["metadata"]["deleted"] is True


class TestOnPluginRemoved:
    """PluginRemoved → remove_namespaced_source + SkillsChanged."""

    async def test_removes_namespaced_source(self, tmp_path: Path) -> None:
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = MagicMock()

        # Pre-populate a skill.
        skill_dir = tmp_path / "skills" / "linter"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Linter\n---\nBody"
        )
        registry.add_namespaced_source("cr", tmp_path / "skills")
        assert "cr:linter" in registry.skills

        skills_changed_count = 0

        async def on_skills_changed(event: SkillsChanged) -> None:
            nonlocal skills_changed_count
            skills_changed_count += 1

        bus.on(SkillsChanged, on_skills_changed)

        register_plugin_event_listeners(bus, registry, session_registry)

        await _dispatch_and_cleanup(bus, PluginRemoved(alias="cr"))

        assert "cr:linter" not in registry.skills
        assert skills_changed_count == 1


class TestBusDispatchOrdering:
    """Verify bubus dispatch ordering: PluginRemoving handler's state is
    visible to PluginRemoved handler."""

    async def test_removing_handler_runs_before_removed(self, tmp_path: Path) -> None:
        """Bus-dispatch ordering test: PluginRemoving handler's state mutation
        is observable by PluginRemoved handler."""
        bus = EventBus()
        registry = SkillRegistry([])
        session_registry = AsyncMock()
        session_registry.get_active_session.return_value = None

        state: dict[str, bool] = {"removing_ran": False, "removed_saw_removing": False}

        register_plugin_event_listeners(bus, registry, session_registry)

        # Add extra handlers to observe ordering.
        async def on_removing(event: PluginRemoving) -> None:
            await asyncio.sleep(0.05)  # Simulate slow handler.
            state["removing_ran"] = True

        async def on_removed(event: PluginRemoved) -> None:
            state["removed_saw_removing"] = state["removing_ran"]

        bus.on(PluginRemoving, on_removing)
        bus.on(PluginRemoved, on_removed)

        # Simulate the remove flow: first dispatch Removing, then Removed.
        await bus.dispatch(PluginRemoving(alias="x", namespaced_skill_names=[]))
        await bus.wait_until_idle()
        await bus.dispatch(PluginRemoved(alias="x"))
        await bus.wait_until_idle()
        await bus.stop()

        assert state["removing_ran"] is True
        assert state["removed_saw_removing"] is True

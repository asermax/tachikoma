"""Tests for handler validation during plugin discovery.

Covers all AC from the "Manifest Extension" block:
- Valid hooks/events parsed and resolved
- CC hooks/events silently ignored
- All validation failure modes (missing file, import error, missing callable,
  wrong signature, unknown event type)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from tachikoma.plugins.loader import discover
from tachikoma.plugins.manifest import TachikomaManifest
from tachikoma.plugins.reconciler import ReconcileOutcome, ReconciliationReport
from tachikoma.plugins.sources import LocalPluginSource

from .conftest import write_native_manifest as _write_native_manifest


def _make_source(tmp_path: Path) -> LocalPluginSource:
    return LocalPluginSource(path=tmp_path / "src")


def _write_handler(
    base: Path,
    subdir: str,
    module_name: str,
    content: str,
) -> Path:
    """Write a handler module file."""
    handler_dir = base / subdir
    handler_dir.mkdir(parents=True, exist_ok=True)
    path = handler_dir / f"{module_name}.py"
    path.write_text(content)
    return path


class TestHandlerValidationSuccess:
    """Valid hooks/events resolve correctly."""

    def test_valid_init_hook_resolved(self, tmp_path: Path) -> None:
        """AC: hooks/init resolves to callable with correct signature."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", hooks={"init": "init"}
        )
        _write_handler(p_dir, "hooks", "init", "def init(ctx): pass\n")

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert loaded[0].init_hook is not None
        assert callable(loaded[0].init_hook)

    def test_valid_event_handler_resolved(self, tmp_path: Path) -> None:
        """AC: events/coordinator_idle resolves to callable with correct signature."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir,
            name="alpha",
            events={"coordinator_idle": "on_idle"},
        )
        _write_handler(
            p_dir, "events", "on_idle", "def handle(event, ctx): pass\n"
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert len(loaded[0].event_handlers) == 1

    def test_hooks_and_events_together(self, tmp_path: Path) -> None:
        """AC: Both hooks and events sections are validated."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir,
            name="alpha",
            hooks={"init": "init"},
            events={"coordinator_idle": "on_idle"},
        )
        _write_handler(p_dir, "hooks", "init", "def init(ctx): pass\n")
        _write_handler(
            p_dir, "events", "on_idle", "def handle(event, ctx): pass\n"
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert loaded[0].init_hook is not None
        assert len(loaded[0].event_handlers) == 1

    def test_multiple_events_captured(self, tmp_path: Path) -> None:
        """AC: Multiple entries in [events] are all captured."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir,
            name="alpha",
            events={
                "coordinator_idle": "on_idle",
                "notification": "on_notify",
            },
        )
        _write_handler(
            p_dir, "events", "on_idle", "def handle(event, ctx): pass\n"
        )
        _write_handler(
            p_dir, "events", "on_notify", "def handle(event, ctx): pass\n"
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert len(loaded[0].event_handlers) == 2


class TestCCPluginHooksIgnored:
    """CC plugins with hooks/events contributions are silently ignored."""

    def test_cc_hooks_ignored(self, tmp_path: Path) -> None:
        """AC: CC plugin with 'hooks' contribution loads normally."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        cc_dir = p_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text(
            '{"name": "alpha", "hooks": {"init": "init.py"}}'
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert loaded[0].init_hook is None
        assert "hooks" in loaded[0].manifest.ignored_cc_contributions

    def test_cc_events_ignored(self, tmp_path: Path) -> None:
        """AC: CC plugin with 'events' contribution loads normally."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        cc_dir = p_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text(
            '{"name": "alpha", "events": {"coordinator_idle": "on_idle.py"}}'
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert len(loaded[0].event_handlers) == 0
        assert "events" in loaded[0].manifest.ignored_cc_contributions


class TestHandlerValidationFailures:
    """All validation failure modes produce diagnostics."""

    def test_missing_hook_file(self, tmp_path: Path) -> None:
        """AC: Missing hooks/init.py → fail with diagnostic naming file."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", hooks={"init": "init"}
        )
        # Don't create hooks/init.py

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "hooks" in loaded[0].diagnostic
        assert "init.py" in loaded[0].diagnostic

    def test_missing_event_file(self, tmp_path: Path) -> None:
        """AC: Missing events/on_idle.py → fail with diagnostic naming file."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", events={"coordinator_idle": "on_idle"}
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "events" in loaded[0].diagnostic
        assert "on_idle.py" in loaded[0].diagnostic

    def test_import_error_in_handler(self, tmp_path: Path) -> None:
        """AC: Syntax error in handler → fail with diagnostic including error."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", hooks={"init": "init"}
        )
        _write_handler(p_dir, "hooks", "init", "def init(ctx\n")  # syntax error

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "import" in loaded[0].diagnostic.lower() or "syntax" in loaded[0].diagnostic.lower()

    def test_missing_init_callable(self, tmp_path: Path) -> None:
        """AC: Module without 'init' function → fail with diagnostic."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", hooks={"init": "init"}
        )
        _write_handler(p_dir, "hooks", "init", "# no init function here\n")

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "init" in loaded[0].diagnostic
        assert "callable" in loaded[0].diagnostic.lower()

    def test_missing_handle_callable(self, tmp_path: Path) -> None:
        """AC: Module without 'handle' function → fail with diagnostic."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", events={"coordinator_idle": "on_idle"}
        )
        _write_handler(p_dir, "events", "on_idle", "# no handle function\n")

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "handle" in loaded[0].diagnostic
        assert "callable" in loaded[0].diagnostic.lower()

    def test_hook_wrong_signature(self, tmp_path: Path) -> None:
        """AC: Init hook with wrong param count → fail with expected signature."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", hooks={"init": "init"}
        )
        _write_handler(p_dir, "hooks", "init", "def init(a, b): pass\n")

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "1 parameter" in loaded[0].diagnostic
        assert "2" in loaded[0].diagnostic

    def test_event_handler_wrong_signature(self, tmp_path: Path) -> None:
        """AC: Event handler with wrong param count → fail with expected signature."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", events={"coordinator_idle": "on_idle"}
        )
        _write_handler(
            p_dir, "events", "on_idle", "def handle(event): pass\n"
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "2 parameter" in loaded[0].diagnostic

    def test_unknown_event_type(self, tmp_path: Path) -> None:
        """AC: Unknown event type name → fail listing valid types."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(
            p_dir, name="alpha", events={"unknown_event": "on_unknown"}
        )
        _write_handler(
            p_dir, "events", "on_unknown", "def handle(event, ctx): pass\n"
        )

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "failed"
        assert "unknown_event" in loaded[0].diagnostic
        assert "coordinator_idle" in loaded[0].diagnostic


class TestManifestModuleValidation:
    """Module name validation at manifest level."""

    def test_path_traversal_in_hooks_rejected(self, tmp_path: Path) -> None:
        """AC: '..' in module name → ValidationError."""
        try:
            TachikomaManifest(
                name="test",
                description="test",
                hooks={"init": "../escape"},
            )
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass

    def test_path_separator_in_events_rejected(self, tmp_path: Path) -> None:
        """AC: Path separator in module name → ValidationError."""
        try:
            TachikomaManifest(
                name="test",
                description="test",
                events={"coordinator_idle": "sub/dir"},
            )
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass

    def test_empty_module_name_rejected(self, tmp_path: Path) -> None:
        """AC: Empty module name → ValidationError."""
        try:
            TachikomaManifest(
                name="test",
                description="test",
                hooks={"init": ""},
            )
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass

    def test_plugin_without_hooks_or_events(self, tmp_path: Path) -> None:
        """AC: Plugin with no hooks/events loads normally."""
        install_dir = tmp_path / "plugins"
        p_dir = install_dir / "alpha"
        _write_native_manifest(p_dir, name="alpha")

        source = _make_source(tmp_path)
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source})
        assert loaded[0].status == "loaded"
        assert loaded[0].init_hook is None
        assert len(loaded[0].event_handlers) == 0

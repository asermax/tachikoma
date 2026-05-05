"""Tests for plugin discovery (loader).

Covers:
- Mixed report scenarios (loaded + stale-fallback + failed)
- Manifest validation: AC-MP-8 (missing skill dir), AC-MP-6 (no manifest)
- Per-plugin failure isolation (R9)
- Config validation during discovery (R3, R4)
"""

from __future__ import annotations

from pathlib import Path

from tachikoma.plugins.loader import discover
from tachikoma.plugins.reconciler import ReconcileOutcome, ReconciliationReport
from tachikoma.plugins.sources import LocalPluginSource

from .conftest import write_native_manifest as _write_native_manifest


def _make_skill_dir(base: Path, skill_name: str) -> Path:
    """Create a skill directory with a minimal SKILL.md."""
    skill_dir = base / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\ndescription: {skill_name}\n---\n# {skill_name}\n")
    return skill_dir


class TestDiscover:
    """Tests for plugin discovery."""

    def test_mixed_report(self, tmp_path: Path) -> None:
        """A mixed report produces correct LoadedPlugin records."""
        install_dir = tmp_path / "plugins"
        install_dir.mkdir()

        # Plugin 1: loaded with a manifest.
        p1_dir = install_dir / "alpha"
        _write_native_manifest(p1_dir, name="alpha")

        # Plugin 2: stale-fallback with a manifest.
        p2_dir = install_dir / "beta"
        _write_native_manifest(p2_dir, name="beta")

        # Plugin 3: failed (no manifest needed for failed).
        p3_dir = install_dir / "gamma"
        p3_dir.mkdir()

        source = LocalPluginSource(path=tmp_path / "src")

        report = ReconciliationReport(
            outcomes=[
                ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None),
                ReconcileOutcome(
                    alias="beta",
                    status="stale-fallback",
                    diagnostic="Source unreachable",
                ),
                ReconcileOutcome(alias="gamma", status="failed", diagnostic="No source"),
            ]
        )

        plugins = {
            "alpha": source,
            "beta": source,
            "gamma": source,
        }

        loaded = discover(install_dir, report, plugins)

        by_alias = {p.alias: p for p in loaded}
        assert by_alias["alpha"].status == "loaded"
        assert by_alias["alpha"].manifest is not None
        assert by_alias["beta"].status == "stale-fallback"
        assert by_alias["beta"].manifest is not None
        assert by_alias["gamma"].status == "failed"
        assert by_alias["gamma"].manifest is None

    def test_ac_mp8_missing_skill_dir_logged_and_excluded(self, tmp_path: Path) -> None:
        """AC-MP-8: missing skill directory is excluded but plugin still loads."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test-plugin"
        skill_present = _make_skill_dir(plugin_dir, "skills/present")
        # Do NOT create "skills/missing" -- it is declared but absent.

        _write_native_manifest(
            plugin_dir,
            name="test-plugin",
            skills=["skills/present", "skills/missing"],
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test-plugin", status="loaded", diagnostic=None)]
        )
        plugins = {"test-plugin": source}

        loaded = discover(install_dir, report, plugins)

        assert len(loaded) == 1
        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.manifest is not None
        # Only the existing skill dir should remain.
        assert skill_present in plugin.manifest.skill_dirs
        assert len(plugin.manifest.skill_dirs) == 1

    def test_ac_mp8_all_skill_dirs_missing_still_loads(self, tmp_path: Path) -> None:
        """AC-MP-8: if all declared skill dirs are missing, plugin still loads with zero skills."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test-plugin"
        _write_native_manifest(
            plugin_dir,
            name="test-plugin",
            skills=["skills/gone"],
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test-plugin", status="loaded", diagnostic=None)]
        )
        plugins = {"test-plugin": source}

        loaded = discover(install_dir, report, plugins)

        assert loaded[0].status == "loaded"
        assert loaded[0].manifest is not None
        assert loaded[0].manifest.skill_dirs == []

    def test_ac_mp6_no_manifest_marks_failed(self, tmp_path: Path) -> None:
        """AC-MP-6: plugin with no manifest is marked failed."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "empty-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        # No manifest files created.

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="empty-plugin", status="loaded", diagnostic=None)]
        )
        plugins = {"empty-plugin": source}

        loaded = discover(install_dir, report, plugins)

        assert loaded[0].status == "failed"
        assert loaded[0].manifest is None
        assert loaded[0].diagnostic is not None
        assert "no manifest" in loaded[0].diagnostic.lower()

    def test_failed_outcome_propagated_without_parsing(self, tmp_path: Path) -> None:
        """A failed outcome from reconciler is propagated directly."""
        install_dir = tmp_path / "plugins"
        install_dir.mkdir()
        (install_dir / "bad-plugin").mkdir()

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[
                ReconcileOutcome(
                    alias="bad-plugin",
                    status="failed",
                    diagnostic="Source unreachable",
                )
            ]
        )
        plugins = {"bad-plugin": source}

        loaded = discover(install_dir, report, plugins)

        assert loaded[0].status == "failed"
        assert loaded[0].diagnostic == "Source unreachable"

    def test_per_plugin_isolation_on_bad_manifest(self, tmp_path: Path) -> None:
        """R9: one bad manifest does not prevent other plugins from loading."""
        install_dir = tmp_path / "plugins"

        # Good plugin.
        good_dir = install_dir / "good"
        _write_native_manifest(good_dir, name="good")

        # Bad plugin: malformed TOML.
        bad_dir = install_dir / "bad"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "tachikoma-plugin.toml").write_text("this is not valid toml {{{{")

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[
                ReconcileOutcome(alias="good", status="loaded", diagnostic=None),
                ReconcileOutcome(alias="bad", status="loaded", diagnostic=None),
            ]
        )
        plugins = {"good": source, "bad": source}

        loaded = discover(install_dir, report, plugins)

        by_alias = {p.alias: p for p in loaded}
        assert by_alias["good"].status == "loaded"
        assert by_alias["good"].manifest is not None
        assert by_alias["bad"].status == "failed"
        assert by_alias["bad"].manifest is None

    def test_loaded_plugin_contributed_skills_is_mutable(self, tmp_path: Path) -> None:
        """contributed_skills starts empty but can be mutated in-place."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test"
        _write_native_manifest(plugin_dir, name="test")

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test", status="loaded", diagnostic=None)]
        )
        plugins = {"test": source}

        loaded = discover(install_dir, report, plugins)
        plugin = loaded[0]

        # Starts empty.
        assert plugin.contributed_skills == []

        # Can be mutated in-place (important for Batch 5 listener).
        plugin.contributed_skills.append("mock-skill")
        assert plugin.contributed_skills == ["mock-skill"]


class TestConfigValidation:
    """Config validation during discovery (R3, R4)."""

    def test_valid_config_loads_with_values(self, tmp_path: Path) -> None:
        """Plugin with config schema + valid user values loads with config dict."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "api_key": {"type": "string", "description": "API key", "required": True},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout",
                    "default": 30,
                },
            },
        )

        source = LocalPluginSource(
            path=tmp_path / "src",
            config={"api_key": "sk-abc", "timeout": 60},
        )
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {"api_key": "sk-abc", "timeout": 60}

    def test_required_field_missing_fails(self, tmp_path: Path) -> None:
        """Plugin with missing required field fails with diagnostic."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "api_key": {"type": "string", "description": "API key", "required": True},
            },
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "failed"
        assert "api_key" in plugin.diagnostic
        assert "missing" in plugin.diagnostic.lower()

    def test_type_mismatch_fails(self, tmp_path: Path) -> None:
        """Plugin with type mismatch fails with diagnostic."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "timeout": {"type": "integer", "description": "Timeout"},
            },
        )

        source = LocalPluginSource(path=tmp_path / "src", config={"timeout": "not a number"})
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "failed"
        assert "timeout" in plugin.diagnostic
        assert "integer" in plugin.diagnostic.lower()

    def test_defaults_applied_no_user_values(self, tmp_path: Path) -> None:
        """Plugin with all defaults and no user values loads with defaults."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "timeout": {"type": "integer", "description": "Timeout", "default": 30},
                "debug": {"type": "boolean", "description": "Debug", "default": False},
            },
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {"timeout": 30, "debug": False}

    def test_optional_no_default_absent_from_config(self, tmp_path: Path) -> None:
        """Optional field without default and no user value is absent from config."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "region": {"type": "string", "description": "Region"},
            },
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert "region" not in plugin.config

    def test_no_config_schema_empty_dict(self, tmp_path: Path) -> None:
        """Plugin with no config schema loads with empty config dict."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "simple"
        _write_native_manifest(plugin_dir, name="simple")

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="simple", status="loaded", diagnostic=None)]
        )
        plugins = {"simple": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {}

    def test_cc_plugin_with_user_config_empty_dict(self, tmp_path: Path) -> None:
        """CC plugin with user config values loads with empty config (no schema)."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "cc-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        cc_dir = plugin_dir / ".claude-plugin"
        cc_dir.mkdir()
        (cc_dir / "plugin.json").write_text('{"name": "cc-plugin"}')

        source = LocalPluginSource(
            path=tmp_path / "src",
            config={"key": "value"},
        )
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="cc-plugin", status="loaded", diagnostic=None)]
        )
        plugins = {"cc-plugin": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {}

    def test_unknown_user_keys_loaded_with_warning(self, tmp_path: Path) -> None:
        """Unknown user keys produce warning, plugin still loads."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "api_key": {"type": "string", "description": "API key", "required": True},
            },
        )

        source = LocalPluginSource(
            path=tmp_path / "src",
            config={"api_key": "sk-abc", "unknown_key": "value"},
        )
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {"api_key": "sk-abc"}
        assert "unknown_key" not in plugin.config

    def test_float_to_integer_coercion(self, tmp_path: Path) -> None:
        """Float 30.0 for integer field is coerced to 30."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "timeout": {"type": "integer", "description": "Timeout", "default": 30},
            },
        )

        # Simulate TOML producing 30.0 for an integer field
        source = LocalPluginSource(path=tmp_path / "src", config={"timeout": 30.0})
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {"timeout": 30}
        assert isinstance(plugin.config["timeout"], int)

    def test_empty_string_valid_value(self, tmp_path: Path) -> None:
        """Empty string for optional string field is valid (distinct from unset)."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "weather"
        _write_native_manifest(
            plugin_dir,
            name="weather",
            config={
                "api_key": {"type": "string", "description": "API key"},
            },
        )

        source = LocalPluginSource(path=tmp_path / "src", config={"api_key": ""})
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="weather", status="loaded", diagnostic=None)]
        )
        plugins = {"weather": source}

        loaded = discover(install_dir, report, plugins)

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {"api_key": ""}

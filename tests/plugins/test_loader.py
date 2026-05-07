"""Tests for plugin discovery (loader).

Covers:
- Mixed report scenarios (loaded + stale-fallback + failed)
- Manifest validation: AC-MP-8 (missing skill dir), AC-MP-6 (no manifest)
- Per-plugin failure isolation (R9)
- Config validation during discovery (R3, R4)
- Post-processor validation and discovery (DLT-050)
"""

from __future__ import annotations

from pathlib import Path

from tachikoma.plugins.loader import (
    _validate_post_processors,
    discover,
)
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.reconciler import ReconcileOutcome, ReconciliationReport
from tachikoma.plugins.sources import LocalPluginSource
from tachikoma.post_processing import (
    MAIN_PHASE,
    PRE_FINALIZE_PHASE,
    PostProcessor,
)

from .conftest import make_agent_defaults
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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))
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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

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

        loaded = discover(install_dir, report, plugins, make_agent_defaults(tmp_path))

        plugin = loaded[0]
        assert plugin.status == "loaded"
        assert plugin.config == {"api_key": ""}


# ---------------------------------------------------------------------------
# Post-processor validation tests
# ---------------------------------------------------------------------------

_BASIC_PROCESSOR_CODE = """\
from tachikoma.post_processing import PostProcessor, MAIN_PHASE

class BasicProcessor(PostProcessor):
    phase = MAIN_PHASE

    def __init__(self, *, config):
        self.config = config

    async def process(self, session, *, extra=None):
        pass
"""

_PROCESSOR_PRE_FINALIZE_CODE = """\
from tachikoma.post_processing import PostProcessor, PRE_FINALIZE_PHASE

class PreFinalizeProcessor(PostProcessor):
    phase = PRE_FINALIZE_PHASE

    def __init__(self, *, config):
        self.config = config

    async def process(self, session, *, extra=None):
        pass
"""

_PROCESSOR_WITH_AGENT_DEFAULTS_CODE = """\
from tachikoma.post_processing import PostProcessor, MAIN_PHASE

class SdkProcessor(PostProcessor):
    phase = MAIN_PHASE

    def __init__(self, *, config, agent_defaults):
        self.config = config
        self.agent_defaults = agent_defaults

    async def process(self, session, *, extra=None):
        pass
"""

_MULTI_PROCESSOR_CODE = """\
from tachikoma.post_processing import PostProcessor, MAIN_PHASE, PRE_FINALIZE_PHASE

class FirstProcessor(PostProcessor):
    phase = MAIN_PHASE

    def __init__(self, *, config):
        self.config = config

    async def process(self, session, *, extra=None):
        pass

class SecondProcessor(PostProcessor):
    phase = PRE_FINALIZE_PHASE

    def __init__(self, *, config):
        self.config = config

    async def process(self, session, *, extra=None):
        pass
"""

_NO_POST_PROCESSOR_CODE = """\
def some_function():
    pass
"""

_INVALID_PHASE_CODE = """\
from tachikoma.post_processing import PostProcessor

class BadPhaseProcessor(PostProcessor):
    phase = "custom_phase"

    def __init__(self, *, config):
        self.config = config

    async def process(self, session, *, extra=None):
        pass
"""

_NO_CONFIG_PARAM_CODE = """\
from tachikoma.post_processing import PostProcessor, MAIN_PHASE

class NoConfigProcessor(PostProcessor):
    phase = MAIN_PHASE

    def __init__(self):
        pass

    async def process(self, session, *, extra=None):
        pass
"""


def _write_processor(base: Path, module_name: str, code: str) -> Path:
    """Write a post-processor module file into post_processors/ directory."""
    processor_dir = base / "post_processors"
    processor_dir.mkdir(parents=True, exist_ok=True)
    path = processor_dir / f"{module_name}.py"
    path.write_text(code)
    return path


def _make_manifest(
    *,
    post_processors: dict[str, str] | None = None,
) -> PluginManifest:
    """Create a PluginManifest for testing post-processor validation."""
    return PluginManifest(
        name="test",
        version="1.0.0",
        description="Test",
        source_format="tachikoma",
        skill_dirs=[],
        post_processors=post_processors or {},
    )


class TestValidatePostProcessors:
    """Post-processor validation via _validate_post_processors()."""

    def test_valid_basic_processor(self, tmp_path: Path) -> None:
        """Valid PostProcessor subclass is accepted and instantiated."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "basic", _BASIC_PROCESSOR_CODE)
        manifest = _make_manifest(post_processors={"basic": "basic"})

        result = _validate_post_processors(
            manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
        )

        assert len(result) == 1
        assert isinstance(result[0], PostProcessor)
        assert result[0].config == {}
        assert result[0].phase == MAIN_PHASE

    def test_valid_processor_with_agent_defaults(self, tmp_path: Path) -> None:
        """Processor accepting agent_defaults receives the AgentDefaults instance."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "sdk", _PROCESSOR_WITH_AGENT_DEFAULTS_CODE)
        manifest = _make_manifest(post_processors={"sdk": "sdk"})

        ad = make_agent_defaults(tmp_path)
        result = _validate_post_processors(manifest, plugin_dir, "test", {}, ad)

        assert len(result) == 1
        assert result[0].agent_defaults is ad

    def test_agent_defaults_not_passed_when_not_accepted(self, tmp_path: Path) -> None:
        """Processor without agent_defaults param does not receive it (no TypeError)."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "basic", _BASIC_PROCESSOR_CODE)
        manifest = _make_manifest(post_processors={"basic": "basic"})

        # Should succeed without TypeError
        result = _validate_post_processors(
            manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
        )

        assert len(result) == 1
        assert not hasattr(result[0], "agent_defaults")

    def test_multiple_classes_in_one_module(self, tmp_path: Path) -> None:
        """Module with multiple PostProcessor subclasses discovers all."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "multi", _MULTI_PROCESSOR_CODE)
        manifest = _make_manifest(post_processors={"multi": "multi"})

        result = _validate_post_processors(
            manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
        )

        assert len(result) == 2
        phases = {p.phase for p in result}
        assert phases == {MAIN_PHASE, PRE_FINALIZE_PHASE}

    def test_module_file_not_found(self, tmp_path: Path) -> None:
        """Missing post_processors/<name>.py raises ValueError."""
        plugin_dir = tmp_path / "plugins" / "test"
        manifest = _make_manifest(post_processors={"missing": "missing"})

        try:
            _validate_post_processors(
                manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
            )
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "not found" in str(exc).lower()

    def test_no_concrete_subclass(self, tmp_path: Path) -> None:
        """Module with no concrete PostProcessor subclass raises ValueError."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "empty", _NO_POST_PROCESSOR_CODE)
        manifest = _make_manifest(post_processors={"empty": "empty"})

        try:
            _validate_post_processors(
                manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
            )
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "No concrete class" in str(exc)

    def test_invalid_phase_value(self, tmp_path: Path) -> None:
        """Processor with invalid phase raises ValueError listing valid phases."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "bad", _INVALID_PHASE_CODE)
        manifest = _make_manifest(post_processors={"bad": "bad"})

        try:
            _validate_post_processors(
                manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
            )
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            msg = str(exc)
            assert "custom_phase" in msg
            assert "main" in msg or "finalize" in msg

    def test_missing_config_parameter(self, tmp_path: Path) -> None:
        """Processor without config param raises ValueError."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "noconfig", _NO_CONFIG_PARAM_CODE)
        manifest = _make_manifest(post_processors={"noconfig": "noconfig"})

        try:
            _validate_post_processors(
                manifest, plugin_dir, "test", {}, make_agent_defaults(tmp_path)
            )
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "config" in str(exc)
            assert "keyword argument" in str(exc)

    def test_empty_manifest_returns_empty_list(self, tmp_path: Path) -> None:
        """Manifest with no post_processors returns empty list."""
        manifest = _make_manifest(post_processors=None)
        result = _validate_post_processors(
            manifest, tmp_path, "test", {}, make_agent_defaults(tmp_path)
        )
        assert result == []

    def test_config_dict_passed_to_processor(self, tmp_path: Path) -> None:
        """Validated config is passed through to processor constructor."""
        plugin_dir = tmp_path / "plugins" / "test"
        _write_processor(plugin_dir, "basic", _BASIC_PROCESSOR_CODE)
        manifest = _make_manifest(post_processors={"basic": "basic"})

        config = {"api_key": "sk-test", "timeout": 30}
        result = _validate_post_processors(
            manifest, plugin_dir, "test", config, make_agent_defaults(tmp_path)
        )

        assert len(result) == 1
        assert result[0].config == config


class TestDiscoverPostProcessors:
    """discover() threads agent_defaults and populates post_processors."""

    def test_discover_populates_post_processors(self, tmp_path: Path) -> None:
        """Plugin with post_processors in manifest populates LoadedPlugin.post_processors."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test"
        _write_processor(plugin_dir, "basic", _BASIC_PROCESSOR_CODE)
        _write_native_manifest(
            plugin_dir,
            name="test",
            post_processors={"basic": "basic"},
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test", status="loaded", diagnostic=None)]
        )

        loaded = discover(install_dir, report, {"test": source}, make_agent_defaults(tmp_path))

        assert loaded[0].status == "loaded"
        assert len(loaded[0].post_processors) == 1
        assert isinstance(loaded[0].post_processors[0], PostProcessor)

    def test_discover_no_post_processors_empty_list(self, tmp_path: Path) -> None:
        """Plugin without post_processors has empty list."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test"
        _write_native_manifest(plugin_dir, name="test")

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test", status="loaded", diagnostic=None)]
        )

        loaded = discover(install_dir, report, {"test": source}, make_agent_defaults(tmp_path))

        assert loaded[0].status == "loaded"
        assert loaded[0].post_processors == []

    def test_discover_invalid_post_processor_fails_plugin(self, tmp_path: Path) -> None:
        """Invalid post-processor causes plugin to fail with diagnostic."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test"
        # Declare a post-processor but don't create the module file
        _write_native_manifest(
            plugin_dir,
            name="test",
            post_processors={"missing": "missing"},
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test", status="loaded", diagnostic=None)]
        )

        loaded = discover(install_dir, report, {"test": source}, make_agent_defaults(tmp_path))

        assert loaded[0].status == "failed"
        assert "Post-processor" in loaded[0].diagnostic

    def test_discover_agent_defaults_threaded_to_processor(self, tmp_path: Path) -> None:
        """agent_defaults is threaded through discover() to processors that accept it."""
        install_dir = tmp_path / "plugins"
        plugin_dir = install_dir / "test"
        _write_processor(plugin_dir, "sdk", _PROCESSOR_WITH_AGENT_DEFAULTS_CODE)
        _write_native_manifest(
            plugin_dir,
            name="test",
            post_processors={"sdk": "sdk"},
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="test", status="loaded", diagnostic=None)]
        )

        ad = make_agent_defaults(tmp_path)
        loaded = discover(install_dir, report, {"test": source}, ad)

        assert loaded[0].status == "loaded"
        assert loaded[0].post_processors[0].agent_defaults is ad

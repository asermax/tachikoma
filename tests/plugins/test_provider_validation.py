"""Tests for provider discovery and validation.

Covers acceptance criteria from Provider Class Discovery and Validation (R1, R2, R6)
and Provider Instantiation (R3).
"""

from __future__ import annotations

from pathlib import Path

from tachikoma.per_message_pre_processing import MessageContextProvider
from tachikoma.plugins.loader import LoadedPlugin, discover
from tachikoma.plugins.reconciler import ReconcileOutcome, ReconciliationReport
from tachikoma.plugins.sources import LocalPluginSource
from tachikoma.pre_processing import ContextProvider

from .conftest import make_agent_defaults, write_native_manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Source code templates for different provider ABC types

_SESSION_PROVIDER_CODE = '''\
from tachikoma.pre_processing import ContextProvider, ContextResult

class TestProvider(ContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message):
        return ContextResult(tag="test", content="data")
    def status_message(self, result=None):
        return "Test provider"
'''

_MESSAGE_PROVIDER_CODE = '''\
from tachikoma.per_message_pre_processing import MessageContextProvider
from tachikoma.pre_processing import ContextResult

class TestMsgProvider(MessageContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message, *, existing_entries=None,
                      sdk_session_id=None, session_summary=None,
                      session_last_exchange=None):
        return [ContextResult(tag="test", content="data")]
    def status_message(self, result=None):
        return "Test msg provider"
'''

_DUAL_ABC_PROVIDER_CODE = '''\
from tachikoma.pre_processing import ContextProvider, ContextResult
from tachikoma.per_message_pre_processing import MessageContextProvider

class DualProvider(ContextProvider, MessageContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message, **kwargs):
        return ContextResult(tag="dual", content="data")
    def status_message(self, result=None):
        return "Dual provider"
'''

_NO_ABC_CODE = '''\
def some_function():
    pass
'''

_ABSTRACT_BASE_CLASS_CODE = '''\
from abc import ABC, abstractmethod
from tachikoma.pre_processing import ContextProvider

class AbstractProvider(ContextProvider, ABC):
    @abstractmethod
    async def provide(self, message):
        ...

    @abstractmethod
    def status_message(self, result=None):
        ...

class ConcreteProvider(ContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message):
        from tachikoma.pre_processing import ContextResult
        return ContextResult(tag="concrete", content="data")
    def status_message(self, result=None):
        return "Concrete provider"
'''

_MULTI_CLASS_CODE = '''\
from tachikoma.pre_processing import ContextProvider, ContextResult
from tachikoma.per_message_pre_processing import MessageContextProvider

class FirstProvider(ContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message):
        return ContextResult(tag="first", content="first data")
    def status_message(self, result=None):
        return "First"

class SecondProvider(MessageContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message, **kwargs):
        return [ContextResult(tag="second", content="second data")]
    def status_message(self, result=None):
        return "Second"
'''

_NO_CONFIG_PARAM_CODE = '''\
from tachikoma.pre_processing import ContextProvider, ContextResult

class NoConfigProvider(ContextProvider):
    def __init__(self):
        pass
    async def provide(self, message):
        return ContextResult(tag="test", content="data")
    def status_message(self, result=None):
        return "No config"
'''

_MISSING_ABSTRACT_METHOD_CODE = '''\
from tachikoma.pre_processing import ContextProvider

class IncompleteProvider(ContextProvider):
    async def provide(self, message):
        from tachikoma.pre_processing import ContextResult
        return ContextResult(tag="test", content="data")
    # Missing status_message()
'''


def _write_provider(base: Path, module_name: str, code: str) -> Path:
    """Write a provider module file into context_providers/ directory."""
    provider_dir = base / "context_providers"
    provider_dir.mkdir(parents=True, exist_ok=True)
    path = provider_dir / f"{module_name}.py"
    path.write_text(code)
    return path


def _make_source(tmp_path: Path) -> LocalPluginSource:
    return LocalPluginSource(path=tmp_path / "src")


def _discover_plugin(
    tmp_path: Path,
    *,
    alias: str = "alpha",
    context_providers: dict[str, str] | None = None,
) -> LoadedPlugin:
    """Helper to discover a single plugin with the given config."""
    install_dir = tmp_path / "plugins"
    p_dir = install_dir / alias
    write_native_manifest(p_dir, name=alias, context_providers=context_providers)

    source = _make_source(tmp_path)
    report = ReconciliationReport(
        outcomes=[ReconcileOutcome(alias=alias, status="loaded", diagnostic=None)]
    )
    loaded = discover(install_dir, report, {alias: source}, make_agent_defaults(tmp_path))
    return loaded[0]


# ---------------------------------------------------------------------------
# Discovery success tests
# ---------------------------------------------------------------------------


class TestProviderDiscoverySuccess:
    """Valid providers are discovered and instantiated correctly."""

    def test_valid_session_provider_discovered(self, tmp_path: Path) -> None:
        """R1: ContextProvider subclass is discovered as session provider."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "calendar", _SESSION_PROVIDER_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"cal": "calendar"})
        assert loaded.status == "loaded"
        assert len(loaded.context_providers) == 1
        assert len(loaded.message_context_providers) == 0
        assert isinstance(loaded.context_providers[0], ContextProvider)

    def test_valid_message_provider_discovered(self, tmp_path: Path) -> None:
        """R1: MessageContextProvider subclass is discovered as message provider."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "crm", _MESSAGE_PROVIDER_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"crm": "crm"})
        assert loaded.status == "loaded"
        assert len(loaded.context_providers) == 0
        assert len(loaded.message_context_providers) == 1
        assert isinstance(loaded.message_context_providers[0], MessageContextProvider)

    def test_dual_abc_provider_discovered(self, tmp_path: Path) -> None:
        """R2: Class implementing both ABCs is discovered for both pipelines."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "dual", _DUAL_ABC_PROVIDER_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"dual": "dual"})
        assert loaded.status == "loaded"
        assert len(loaded.context_providers) == 1
        assert len(loaded.message_context_providers) == 1
        # Same instance in both lists
        assert loaded.context_providers[0] is loaded.message_context_providers[0]

    def test_multi_class_module_discovered(self, tmp_path: Path) -> None:
        """R11: Module with multiple provider classes discovers all concrete ones."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "multi", _MULTI_CLASS_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"multi": "multi"})
        assert loaded.status == "loaded"
        assert len(loaded.context_providers) == 1  # FirstProvider (ContextProvider)
        assert len(loaded.message_context_providers) == 1  # SecondProvider (MessageContextProvider)

    def test_multiple_manifest_entries(self, tmp_path: Path) -> None:
        """R11: Multiple manifest entries each discover their providers."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "calendar", _SESSION_PROVIDER_CODE)
        _write_provider(p_dir, "crm", _MESSAGE_PROVIDER_CODE)

        loaded = _discover_plugin(
            tmp_path, context_providers={"calendar": "calendar", "crm": "crm"}
        )
        assert loaded.status == "loaded"
        assert len(loaded.context_providers) == 1
        assert len(loaded.message_context_providers) == 1

    def test_abstract_classes_skipped(self, tmp_path: Path) -> None:
        """R6: Abstract base classes are skipped, only concrete classes registered."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "mixed", _ABSTRACT_BASE_CLASS_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"mixed": "mixed"})
        assert loaded.status == "loaded"
        # Only the ConcreteProvider, not AbstractProvider
        assert len(loaded.context_providers) == 1


# ---------------------------------------------------------------------------
# Validation failure tests
# ---------------------------------------------------------------------------


class TestProviderValidationFailures:
    """All validation failure modes produce diagnostics."""

    def test_missing_provider_file(self, tmp_path: Path) -> None:
        """R6: Missing context_providers/calendar.py → fail with diagnostic."""
        loaded = _discover_plugin(tmp_path, context_providers={"cal": "calendar"})
        assert loaded.status == "failed"
        assert "calendar.py" in loaded.diagnostic
        assert "not found" in loaded.diagnostic.lower() or "not found" in loaded.diagnostic

    def test_import_error_in_provider(self, tmp_path: Path) -> None:
        """R6: Syntax error in provider module → fail with diagnostic."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "calendar", "def broken(\n")

        loaded = _discover_plugin(tmp_path, context_providers={"cal": "calendar"})
        assert loaded.status == "failed"
        assert "Provider validation" in loaded.diagnostic

    def test_no_valid_class_in_module(self, tmp_path: Path) -> None:
        """R6: Module with no class implementing either ABC → fail."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "calendar", _NO_ABC_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"cal": "calendar"})
        assert loaded.status == "failed"
        assert "No concrete class" in loaded.diagnostic

    def test_missing_config_parameter(self, tmp_path: Path) -> None:
        """R3: Provider __init__ without config param → fail with diagnostic."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "nconfig", _NO_CONFIG_PARAM_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"nc": "nconfig"})
        assert loaded.status == "failed"
        assert "config" in loaded.diagnostic
        assert "keyword argument" in loaded.diagnostic

    def test_abstract_method_typeerror_caught(self, tmp_path: Path) -> None:
        """R6: Provider class with missing abstract method → ValueError."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "incomplete", _MISSING_ABSTRACT_METHOD_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"inc": "incomplete"})
        assert loaded.status == "failed"
        assert "Provider validation" in loaded.diagnostic


# ---------------------------------------------------------------------------
# Instantiation tests
# ---------------------------------------------------------------------------


class TestProviderInstantiation:
    """Provider instantiation receives correct config."""

    def test_config_dict_passed(self, tmp_path: Path) -> None:
        """R3: Provider receives validated config dict."""
        code = '''\
from tachikoma.pre_processing import ContextProvider, ContextResult

class ConfigCaptureProvider(ContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message):
        return ContextResult(tag="test", content=str(self.config))
    def status_message(self, result=None):
        return "Config capture"
'''
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "capture", code)

        # Create plugin with config schema
        install_dir = tmp_path / "plugins"
        p_dir2 = install_dir / "alpha"
        write_native_manifest(
            p_dir2,
            name="alpha",
            context_providers={"cap": "capture"},
            config={
                "key": {
                    "type": "string",
                    "description": "A key",
                    "default": "val",
                },
            },
        )

        source = LocalPluginSource(path=tmp_path / "src")
        report = ReconciliationReport(
            outcomes=[ReconcileOutcome(alias="alpha", status="loaded", diagnostic=None)]
        )
        loaded = discover(install_dir, report, {"alpha": source}, make_agent_defaults(tmp_path))

        assert loaded[0].status == "loaded"
        assert loaded[0].context_providers[0].config == {"key": "val"}

    def test_empty_dict_for_no_schema(self, tmp_path: Path) -> None:
        """R3: Plugin with no config schema passes empty dict."""
        code = '''\
from tachikoma.pre_processing import ContextProvider, ContextResult

class ConfigCaptureProvider(ContextProvider):
    def __init__(self, *, config):
        self.config = config
    async def provide(self, message):
        return ContextResult(tag="test", content=str(self.config))
    def status_message(self, result=None):
        return "Config capture"
'''
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "capture", code)

        loaded = _discover_plugin(tmp_path, context_providers={"cap": "capture"})
        assert loaded.status == "loaded"
        assert loaded.context_providers[0].config == {}

    def test_dual_abc_same_instance(self, tmp_path: Path) -> None:
        """R2: Dual-ABC provider is same object in both lists."""
        p_dir = tmp_path / "plugins" / "alpha"
        _write_provider(p_dir, "dual", _DUAL_ABC_PROVIDER_CODE)

        loaded = _discover_plugin(tmp_path, context_providers={"d": "dual"})
        assert loaded.status == "loaded"
        assert loaded.context_providers[0] is loaded.message_context_providers[0]

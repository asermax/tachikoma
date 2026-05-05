"""Tests for plugin MCP tools.

Covers arg validation, handler logic, and factory smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tachikoma.plugins.config_schema import ConfigFieldSchema
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manager import (
    PluginAliasCollisionError,
    PluginInstallError,
    PluginManager,
    PluginNotFoundError,
)
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.sources import GitPluginSource, LocalPluginSource
from tachikoma.plugins.tools import (
    InstallPluginArgs,
    RemovePluginArgs,
    create_plugin_tools_server,
    handle_install_plugin,
    handle_list_plugins,
    handle_remove_plugin,
)

from .conftest import make_plugin as _make_plugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(loaded: dict[str, LoadedPlugin] | None = None) -> PluginManager:
    sm = MagicMock()
    sm.settings = MagicMock()
    sm.settings.plugins = {}
    bus = MagicMock()
    bus.dispatch = AsyncMock()
    return PluginManager(
        settings_manager=sm,
        bus=bus,
        workspace_path=Path("/tmp/workspace"),
        loaded=loaded or {},
    )


def _parse_content(result: dict) -> dict | list:
    """Extract JSON content from MCP envelope."""
    text = result["content"][0]["text"]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Step 6.1: Arg models
# ---------------------------------------------------------------------------


class TestInstallPluginArgs:
    def test_valid_git(self) -> None:
        args = InstallPluginArgs(git="https://github.com/foo/bar")
        assert args.git == "https://github.com/foo/bar"
        assert args.url is None
        assert args.path is None

    def test_valid_url(self) -> None:
        args = InstallPluginArgs(url="https://example.com/plugin.tar.gz")
        assert args.url == "https://example.com/plugin.tar.gz"

    def test_valid_path(self) -> None:
        args = InstallPluginArgs(path="/some/local/dir")
        assert args.path == "/some/local/dir"

    def test_with_optional_fields(self) -> None:
        args = InstallPluginArgs(
            git="https://github.com/foo/bar",
            subdir="sub",
            ref="v1",
            alias="my",
        )
        assert args.subdir == "sub"
        assert args.ref == "v1"
        assert args.alias == "my"

    def test_no_source_raises(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            InstallPluginArgs()

    def test_multiple_sources_raises(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            InstallPluginArgs(git="https://github.com/foo", url="https://example.com/p.tar.gz")


class TestRemovePluginArgs:
    def test_valid(self) -> None:
        args = RemovePluginArgs(alias="test-plugin")
        assert args.alias == "test-plugin"


# ---------------------------------------------------------------------------
# Step 6.2: Handler functions
# ---------------------------------------------------------------------------


class TestHandleInstallPlugin:
    """AC-MCP-INST-1..6 via handler calls (mocked manager)."""

    async def test_success(self) -> None:
        plugin = _make_plugin("my-plugin")
        manager = _make_manager()
        manager.install = AsyncMock(return_value=plugin)

        args = InstallPluginArgs(path="/some/plugin")
        result = await handle_install_plugin(args, manager)

        assert result.get("is_error") is None
        payload = _parse_content(result)
        assert payload["alias"] == "my-plugin"
        assert payload["status"] == "loaded"

    async def test_collision_with_retry_hint(self) -> None:
        manager = _make_manager()
        manager.install = AsyncMock(
            side_effect=PluginAliasCollisionError("linter", suggest_retry_with_alias=True)
        )

        args = InstallPluginArgs(path="/some/plugin")
        result = await handle_install_plugin(args, manager)

        assert result["is_error"] is True
        assert "already exists" in result["content"][0]["text"]
        assert "retry" in result["content"][0]["text"].lower()

    async def test_collision_no_retry_hint(self) -> None:
        manager = _make_manager()
        manager.install = AsyncMock(
            side_effect=PluginAliasCollisionError("my-cr", suggest_retry_with_alias=False)
        )

        args = InstallPluginArgs(path="/some/plugin", alias="my-cr")
        result = await handle_install_plugin(args, manager)

        assert result["is_error"] is True
        assert "already exists" in result["content"][0]["text"]

    async def test_install_error(self) -> None:
        manager = _make_manager()
        manager.install = AsyncMock(
            side_effect=PluginInstallError("bad-plugin", RuntimeError("disk full"))
        )

        args = InstallPluginArgs(path="/some/plugin")
        result = await handle_install_plugin(args, manager)

        assert result["is_error"] is True
        assert "Install failed" in result["content"][0]["text"]

    async def test_invalid_source(self) -> None:
        # Use a git URL that will fail parse_plugin_source validation
        args = InstallPluginArgs(git="not-a-valid-sha-ref")
        manager = _make_manager()

        result = await handle_install_plugin(args, manager)

        assert result["is_error"] is True
        assert "Invalid source" in result["content"][0]["text"]


class TestHandleListPlugins:
    """AC-MCP-LIST-1..3."""

    async def test_empty_list(self) -> None:
        manager = _make_manager()
        result = await handle_list_plugins(manager)

        assert result.get("is_error") is None
        assert result["content"][0]["text"] == "No plugins installed."

    async def test_returns_plugin_info(self) -> None:
        plugin = _make_plugin("linter")
        manager = _make_manager(loaded={"linter": plugin})

        result = await handle_list_plugins(manager)

        assert result.get("is_error") is None
        payload = _parse_content(result)
        assert len(payload) == 1
        assert payload[0]["alias"] == "linter"
        assert payload[0]["status"] == "loaded"

    async def test_shows_diagnostic_for_non_loaded(self) -> None:
        plugin = LoadedPlugin(
            alias="stale",
            source=GitPluginSource(git="https://github.com/foo/bar", ref="main"),
            manifest=PluginManifest(
                name="stale",
                version="1.0.0",
                description="Stale plugin",
                source_format="tachikoma",
                skill_dirs=[],
            ),
            status="stale-fallback",
            diagnostic="git fetch failed",
            plugin_dir=Path("/tmp/plugins/stale"),
        )
        manager = _make_manager(loaded={"stale": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        assert payload[0]["status"] == "stale-fallback"
        assert payload[0]["diagnostic"] == "git fetch failed"


class TestHandleRemovePlugin:
    """AC-MCP-REM-1..4."""

    async def test_success(self) -> None:
        manager = _make_manager()
        manager.remove = AsyncMock(return_value=None)

        args = RemovePluginArgs(alias="test-plugin")
        result = await handle_remove_plugin(args, manager)

        assert result.get("is_error") is None
        payload = _parse_content(result)
        assert payload["alias"] == "test-plugin"
        assert payload["removed"] is True

    async def test_success_with_diagnostic(self) -> None:
        manager = _make_manager()
        manager.remove = AsyncMock(return_value="Directory removal failed: perm denied")

        args = RemovePluginArgs(alias="fragile")
        result = await handle_remove_plugin(args, manager)

        payload = _parse_content(result)
        assert payload["diagnostic"] is not None

    async def test_not_found(self) -> None:
        manager = _make_manager()
        manager.remove = AsyncMock(side_effect=PluginNotFoundError("nonexistent"))

        args = RemovePluginArgs(alias="nonexistent")
        result = await handle_remove_plugin(args, manager)

        assert result["is_error"] is True
        assert "nonexistent" in result["content"][0]["text"]


class TestCreatePluginToolsServer:
    """Smoke test: factory returns a server config without raising."""

    def test_factory_returns_config(self) -> None:
        manager = _make_manager()
        config = create_plugin_tools_server(manager)
        assert config is not None


# ---------------------------------------------------------------------------
# Step 13: Config surfacing in list_plugins
# ---------------------------------------------------------------------------


class TestHandleListPluginsConfig:
    """R5 acceptance criteria — config info in list_plugins output."""

    async def test_loaded_plugin_with_config_shows_schema_and_values(self) -> None:
        schema = {
            "api_key": ConfigFieldSchema(
                type="string", description="API key", required=True
            ),
            "timeout": ConfigFieldSchema(
                type="integer", description="Request timeout", default=30
            ),
        }
        plugin = _make_plugin(
            "weather",
            config={"api_key": "sk-abc", "timeout": 60},
            config_schema=schema,
        )
        manager = _make_manager(loaded={"weather": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        entry = payload[0]
        assert "config" in entry
        cfg = entry["config"]

        # Required string field with user value.
        assert cfg["api_key"]["type"] == "string"
        assert cfg["api_key"]["description"] == "API key"
        assert cfg["api_key"]["required"] is True
        assert cfg["api_key"]["value"] == "sk-abc"
        assert "default" not in cfg["api_key"]

        # Optional integer field with default, user override.
        assert cfg["timeout"]["type"] == "integer"
        assert cfg["timeout"]["description"] == "Request timeout"
        assert cfg["timeout"]["required"] is False
        assert cfg["timeout"]["default"] == 30
        assert cfg["timeout"]["value"] == 60

    async def test_plugin_without_config_schema_omits_config_key(self) -> None:
        plugin = _make_plugin("bare")
        manager = _make_manager(loaded={"bare": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        assert "config" not in payload[0]

    async def test_optional_field_uses_default_when_no_user_value(self) -> None:
        schema = {
            "debug": ConfigFieldSchema(
                type="boolean", description="Enable debug", default=False
            ),
        }
        plugin = _make_plugin("svc", config={"debug": False}, config_schema=schema)
        manager = _make_manager(loaded={"svc": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        assert payload[0]["config"]["debug"]["value"] is False
        assert payload[0]["config"]["debug"]["default"] is False

    async def test_optional_field_no_default_no_user_value_shows_null(self) -> None:
        schema = {
            "region": ConfigFieldSchema(
                type="string", description="Region override"
            ),
        }
        plugin = _make_plugin("svc", config={}, config_schema=schema)
        manager = _make_manager(loaded={"svc": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        assert payload[0]["config"]["region"]["value"] is None

    async def test_failed_plugin_shows_schema_and_validation_error(self) -> None:
        schema = {
            "api_key": ConfigFieldSchema(
                type="string", description="API key", required=True
            ),
        }
        plugin = _make_plugin(
            "weather",
            status="failed",
            config={},
            config_schema=schema,
            diagnostic="Required config field 'api_key' is missing.",
        )
        manager = _make_manager(loaded={"weather": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        entry = payload[0]
        assert entry["status"] == "failed"
        assert "config" in entry
        # Schema is still shown so user can see what's expected.
        assert entry["config"]["api_key"]["type"] == "string"
        assert entry["config"]["api_key"]["required"] is True
        assert entry["config"]["api_key"]["value"] is None
        assert entry["config"]["validation_error"] == "Required config field 'api_key' is missing."

    async def test_plugin_with_no_manifest_omits_config_key(self) -> None:
        plugin = LoadedPlugin(
            alias="broken",
            source=LocalPluginSource(path=Path("/tmp/dummy")),
            manifest=None,
            status="failed",
            diagnostic="No manifest found",
            plugin_dir=Path("/tmp/plugins/broken"),
        )
        manager = _make_manager(loaded={"broken": plugin})

        result = await handle_list_plugins(manager)

        payload = _parse_content(result)
        assert "config" not in payload[0]

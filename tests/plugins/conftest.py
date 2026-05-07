"""Shared fixtures and helpers for plugin tests."""

from __future__ import annotations

from pathlib import Path

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.plugins.config_schema import ConfigFieldSchema
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.sources import LocalPluginSource


def make_agent_defaults(tmp_path: Path | None = None) -> AgentDefaults:
    """Create a minimal AgentDefaults for testing."""
    return AgentDefaults(cwd=tmp_path or Path("/tmp/test-workspace"))


def write_native_manifest(
    plugin_dir: Path,
    *,
    name: str = "test-plugin",
    version: str | None = "1.0.0",
    description: str = "A test plugin",
    skills: list[str] | None = None,
    config: dict[str, dict[str, object]] | None = None,
    hooks: dict[str, str] | None = None,
    events: dict[str, str] | None = None,
    context_providers: dict[str, str] | None = None,
    post_processors: dict[str, str] | None = None,
) -> Path:
    """Write a ``tachikoma-plugin.toml`` into *plugin_dir*."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f'name = "{name}"',
        f'description = "{description}"',
    ]
    if version is not None:
        lines.append(f'version = "{version}"')
    if skills is not None:
        lines.append(f"skills = {skills!r}")
    if config is not None:
        for field_name, field_def in config.items():
            lines.append("")
            lines.append(f"[config.{field_name}]")
            for key, val in field_def.items():
                if isinstance(val, str):
                    lines.append(f'{key} = "{val}"')
                elif isinstance(val, bool):
                    lines.append(f"{key} = {str(val).lower()}")
                else:
                    lines.append(f"{key} = {val}")
    if hooks is not None:
        lines.append("")
        lines.append("[hooks]")
        for key, val in hooks.items():
            lines.append(f'{key} = "{val}"')
    if events is not None:
        lines.append("")
        lines.append("[events]")
        for key, val in events.items():
            lines.append(f'{key} = "{val}"')
    if context_providers is not None:
        lines.append("")
        lines.append("[context_providers]")
        for key, val in context_providers.items():
            lines.append(f'{key} = "{val}"')
    if post_processors is not None:
        lines.append("")
        lines.append("[post_processors]")
        for key, val in post_processors.items():
            lines.append(f'{key} = "{val}"')
    toml_path = plugin_dir / "tachikoma-plugin.toml"
    toml_path.write_text("\n".join(lines) + "\n")
    return toml_path


def make_plugin(
    alias: str,
    *,
    status: str = "loaded",
    config: dict[str, str | int | bool | float] | None = None,
    config_schema: dict[str, ConfigFieldSchema] | None = None,
    diagnostic: str | None = None,
) -> LoadedPlugin:
    """Create a ``LoadedPlugin`` for testing."""
    return LoadedPlugin(
        alias=alias,
        source=LocalPluginSource(path=Path("/tmp/dummy")),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test plugin",
            source_format="tachikoma",
            skill_dirs=[],
            config_schema=config_schema or {},
        ),
        status=status,
        diagnostic=diagnostic,
        plugin_dir=Path(f"/tmp/plugins/{alias}"),
        config=config if config is not None else {},
    )

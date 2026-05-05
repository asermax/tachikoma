"""Shared fixtures and helpers for plugin tests."""

from __future__ import annotations

from pathlib import Path

from tachikoma.plugins.config_schema import ConfigFieldSchema
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.sources import LocalPluginSource


def write_native_manifest(
    plugin_dir: Path,
    *,
    name: str = "test-plugin",
    version: str | None = "1.0.0",
    description: str = "A test plugin",
    skills: list[str] | None = None,
    config: dict[str, dict[str, object]] | None = None,
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

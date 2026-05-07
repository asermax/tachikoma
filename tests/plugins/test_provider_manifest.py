"""Tests for context_providers manifest parsing.

Covers acceptance criteria from the Manifest Declaration section:
- R5: Manifest declares context_providers in a [context_providers] section
- R10: CC plugins declaring context_providers have entries silently ignored
- R11: Multiple context providers per plugin supported
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tachikoma.plugins.manifest import (
    TachikomaManifest,
    log_ignored_cc_contributions,
    parse_manifest,
)

from .conftest import write_native_manifest


class TestManifestContextProviders:
    """Manifest parsing for [context_providers] section."""

    def test_native_manifest_with_single_provider(self, tmp_path: Path) -> None:
        """R5: Single entry in [context_providers] is captured."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        write_native_manifest(plugin_dir, context_providers={"calendar": "calendar"})

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.context_providers == {"calendar": "calendar"}

    def test_native_manifest_with_multiple_providers(self, tmp_path: Path) -> None:
        """R11: Multiple entries in [context_providers] are captured."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        write_native_manifest(
            plugin_dir,
            context_providers={"calendar": "calendar", "crm": "crm"},
        )

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.context_providers == {"calendar": "calendar", "crm": "crm"}

    def test_missing_context_providers_section_gives_empty_dict(self, tmp_path: Path) -> None:
        """R5: Manifest without [context_providers] loads with empty dict."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        write_native_manifest(plugin_dir)

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.context_providers == {}

    def test_cc_manifest_context_providers_ignored(self, tmp_path: Path) -> None:
        """R10: CC plugin with context_providers contribution silently ignored."""
        plugin_dir = tmp_path / "plugin"
        cc_dir = plugin_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text(
            '{"name": "cc-test", "context_providers": {"calendar": "calendar"}}'
        )

        result = parse_manifest(plugin_dir)
        assert result is not None
        assert result.source_format == "cc"
        assert result.context_providers == {}
        assert "context_providers" in result.ignored_cc_contributions

    def test_cc_context_providers_logged_as_ignored(self, tmp_path: Path) -> None:
        """R10: CC context_providers appears in logged ignored contributions."""
        plugin_dir = tmp_path / "plugin"
        cc_dir = plugin_dir / ".claude-plugin"
        cc_dir.mkdir(parents=True)
        (cc_dir / "plugin.json").write_text(
            '{"name": "cc-test", "context_providers": {"cal": "cal"}}'
        )

        result = parse_manifest(plugin_dir)
        assert result is not None
        logged = log_ignored_cc_contributions("test-alias", result)
        assert "context_providers" in logged

    def test_path_traversal_in_module_name_rejected(self) -> None:
        """R5: Module name with '..' is rejected by Pydantic validation."""
        with pytest.raises(ValidationError, match=r"'\.\.' segments"):
            TachikomaManifest(
                name="test",
                description="test",
                context_providers={"evil": "../escape"},
            )

    def test_path_separator_in_module_name_rejected(self) -> None:
        """R5: Module name with path separator is rejected."""
        with pytest.raises(ValidationError, match="bare name"):
            TachikomaManifest(
                name="test",
                description="test",
                context_providers={"sub": "sub/dir"},
            )

    def test_empty_module_name_rejected(self) -> None:
        """R5: Empty module name is rejected."""
        with pytest.raises(ValidationError, match="must not be empty"):
            TachikomaManifest(
                name="test",
                description="test",
                context_providers={"bad": ""},
            )

    def test_absolute_module_name_rejected(self) -> None:
        """R5: Absolute module name is rejected."""
        with pytest.raises(ValidationError, match="absolute"):
            TachikomaManifest(
                name="test",
                description="test",
                context_providers={"bad": "/abs/path"},
            )

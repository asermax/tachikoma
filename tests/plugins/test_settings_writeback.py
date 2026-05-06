"""Tests for plugin config write-back via SettingsManager.

Covers TOML write-back helpers: add/remove round-trips, comment preservation,
KeyError for unknown aliases, and round-trip determinism.
"""

import tomllib
from pathlib import Path

import pytest
import tomlkit

from tachikoma.config import SettingsManager
from tachikoma.plugins.sources import (
    GitPluginSource,
    LocalPluginSource,
    UrlPluginSource,
)


class TestUpdatePluginEntry:
    """Tests for SettingsManager.update_plugin_entry."""

    def test_add_git_entry_round_trip(self, settings_manager: SettingsManager) -> None:
        """Adding a git plugin entry round-trips through doc and settings."""
        source = GitPluginSource(
            git="https://github.com/owner/repo.git",
            subdir="plugin",
            ref="v1.0.0",
        )

        settings_manager.update_plugin_entry("code-review", source)

        # Settings reflect the change
        assert "code-review" in settings_manager.settings.plugins
        plugin = settings_manager.settings.plugins["code-review"]
        assert isinstance(plugin, GitPluginSource)
        assert plugin.git == "https://github.com/owner/repo.git"
        assert plugin.subdir == "plugin"
        assert plugin.ref == "v1.0.0"

        # TOML file has the sub-table
        content = settings_manager._config_path.read_text()
        assert "[plugins.code-review]" in content

    def test_add_url_entry_round_trip(self, settings_manager: SettingsManager) -> None:
        """Adding a URL plugin entry round-trips correctly."""
        source = UrlPluginSource(url="https://example.com/plugin.tar.gz")

        settings_manager.update_plugin_entry("docs-pack", source)

        assert "docs-pack" in settings_manager.settings.plugins
        plugin = settings_manager.settings.plugins["docs-pack"]
        assert isinstance(plugin, UrlPluginSource)
        assert plugin.url == "https://example.com/plugin.tar.gz"

    def test_add_local_entry_round_trip(self, settings_manager: SettingsManager) -> None:
        """Adding a local plugin entry round-trips correctly."""
        source = LocalPluginSource(path=Path("/home/user/dev/my-plugin"))

        settings_manager.update_plugin_entry("dev-plugin", source)

        assert "dev-plugin" in settings_manager.settings.plugins
        plugin = settings_manager.settings.plugins["dev-plugin"]
        assert isinstance(plugin, LocalPluginSource)
        assert plugin.path == Path("/home/user/dev/my-plugin")

    def test_comments_preserved_on_add(self, tmp_path: Path) -> None:
        """Comments and formatting are preserved when adding a plugin entry."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('# User comment\n[workspace]\npath = "~/tachikoma"\n')

        manager = SettingsManager(config_path)
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")
        manager.update_plugin_entry("my-plugin", source)

        content = config_path.read_text()
        assert "# User comment" in content
        assert "[plugins.my-plugin]" in content

    def test_invalid_alias_rejected(self, settings_manager: SettingsManager) -> None:
        """An invalid alias raises ValueError."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")

        with pytest.raises(ValueError, match="Invalid plugin alias"):
            settings_manager.update_plugin_entry("Bad:Alias", source)

    def test_add_multiple_entries(self, settings_manager: SettingsManager) -> None:
        """Multiple entries can be added sequentially."""
        source1 = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")
        source2 = LocalPluginSource(path=Path("/home/user/plugin"))

        settings_manager.update_plugin_entry("git-plugin", source1)
        settings_manager.update_plugin_entry("local-plugin", source2)

        assert len(settings_manager.settings.plugins) == 2
        assert "git-plugin" in settings_manager.settings.plugins
        assert "local-plugin" in settings_manager.settings.plugins

    def test_update_existing_entry(self, settings_manager: SettingsManager) -> None:
        """Updating an existing entry replaces it."""
        source_v1 = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")
        source_v2 = GitPluginSource(git="https://github.com/owner/repo.git", ref="v2.0.0")

        settings_manager.update_plugin_entry("my-plugin", source_v1)
        settings_manager.update_plugin_entry("my-plugin", source_v2)

        plugin = settings_manager.settings.plugins["my-plugin"]
        assert isinstance(plugin, GitPluginSource)
        assert plugin.ref == "v2.0.0"


class TestRemovePluginEntry:
    """Tests for SettingsManager.remove_plugin_entry."""

    def test_remove_existing_entry(self, settings_manager: SettingsManager) -> None:
        """Removing an existing entry removes it from settings and TOML."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")
        settings_manager.update_plugin_entry("code-review", source)

        settings_manager.remove_plugin_entry("code-review")

        assert "code-review" not in settings_manager.settings.plugins
        content = settings_manager._config_path.read_text()
        assert "[plugins.code-review]" not in content

    def test_remove_unknown_raises_key_error(self, settings_manager: SettingsManager) -> None:
        """Removing a non-existent alias raises KeyError."""
        with pytest.raises(KeyError, match="unknown"):
            settings_manager.remove_plugin_entry("unknown")

    def test_remove_from_empty_config_raises_key_error(
        self, settings_manager: SettingsManager
    ) -> None:
        """Removing from config with no [plugins] section raises KeyError."""
        with pytest.raises(KeyError):
            settings_manager.remove_plugin_entry("anything")

    def test_remove_collapses_empty_parent(self, settings_manager: SettingsManager) -> None:
        """Removing the last entry collapses the [plugins] super-table."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")
        settings_manager.update_plugin_entry("only-plugin", source)

        settings_manager.remove_plugin_entry("only-plugin")

        content = settings_manager._config_path.read_text()
        # No stray [plugins] header should remain
        assert "[plugins]" not in content

    def test_comments_preserved_on_remove(self, tmp_path: Path) -> None:
        """Comments and formatting are preserved when removing a plugin entry."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('# User comment\n[workspace]\npath = "~/tachikoma"\n')

        manager = SettingsManager(config_path)
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")
        manager.update_plugin_entry("temp-plugin", source)
        manager.remove_plugin_entry("temp-plugin")

        content = config_path.read_text()
        assert "# User comment" in content


class TestRoundTripDeterminism:
    """Tests for install -> remove -> install determinism."""

    def test_install_remove_install_deterministic(self, settings_manager: SettingsManager) -> None:
        """Install, remove, re-install of same plugin yields consistent state."""
        source = GitPluginSource(
            git="https://github.com/owner/repo.git",
            subdir="plugin",
            ref="v1.0.0",
        )

        # First install
        settings_manager.update_plugin_entry("my-plugin", source)
        content_after_first = settings_manager._config_path.read_text()

        # Remove
        settings_manager.remove_plugin_entry("my-plugin")

        # Re-install
        settings_manager.update_plugin_entry("my-plugin", source)
        content_after_reinstall = settings_manager._config_path.read_text()

        # The re-installed state should be equivalent to the first install
        # (no stranded headers, consistent formatting)
        assert "[plugins.my-plugin]" in content_after_reinstall
        assert content_after_reinstall == content_after_first

        # Settings also match
        assert "my-plugin" in settings_manager.settings.plugins

    def test_toml_is_valid_after_operations(self, settings_manager: SettingsManager) -> None:
        """TOML output is parseable after multiple operations."""
        source = GitPluginSource(git="https://github.com/owner/repo.git", ref="v1.0.0")

        settings_manager.update_plugin_entry("plugin-a", source)
        settings_manager.update_plugin_entry("plugin-b", source)
        settings_manager.remove_plugin_entry("plugin-a")

        content = settings_manager._config_path.read_text()

        # Should parse without error
        data = tomllib.loads(content)
        assert "plugins" not in data or "plugin-b" in data.get("plugins", {})

    def test_tomlkit_doc_round_trip(self, tmp_path: Path) -> None:
        """Verify tomlkit document structure after update_plugin_entry."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[workspace]\npath = "~/tachikoma"\n')

        manager = SettingsManager(config_path)
        source = GitPluginSource(
            git="gh:owner/repo",
            ref="v1.0.0",
        )
        manager.update_plugin_entry("code-review", source)

        # Re-parse the file with tomlkit to verify structure
        doc = tomlkit.parse(config_path.read_text())
        assert "plugins" in doc
        plugins = doc["plugins"]
        assert "code-review" in plugins
        # The TOML file stores the expanded URL, not the shorthand
        entry = plugins["code-review"]
        assert entry["git"] == "https://github.com/owner/repo.git"
        assert entry["ref"] == "v1.0.0"

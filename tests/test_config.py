"""Configuration module tests.

Tests for DLT-012: Configure application parameters and secrets.
"""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from tachikoma.config import (
    Settings,
    WorkspaceSettings,
    _generate_default_config,
    load_settings,
)


class TestSettingsModel:
    """Tests for the Settings model hierarchy."""

    def test_default_workspace_path(self) -> None:
        """AC (R2): workspace.path defaults to ~/tachikoma."""
        settings = Settings()

        assert settings.workspace.path == Path.home() / "tachikoma"

    def test_workspace_path_expands_tilde(self) -> None:
        """AC (R2): Path with ~ is expanded to home directory."""
        ws = WorkspaceSettings(path="~/custom")

        assert ws.path == Path.home() / "custom"

    def test_workspace_path_expands_tilde_from_path(self) -> None:
        ws = WorkspaceSettings(path=Path("~/custom"))

        assert ws.path == Path.home() / "custom"

    def test_default_agent_model_is_none(self) -> None:
        """AC (R2): agent.model defaults to None (SDK default)."""
        settings = Settings()

        assert settings.agent.model is None

    def test_default_agent_allowed_tools(self) -> None:
        """AC (R2): agent.allowed_tools defaults to Read, Glob, Grep."""
        settings = Settings()

        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_frozen_prevents_mutation(self) -> None:
        """Settings instances are immutable."""
        settings = Settings()

        with pytest.raises(ValidationError):
            settings.workspace = WorkspaceSettings(path="/other")

    def test_extra_fields_ignored(self) -> None:
        """AC (R3): Unknown keys are silently ignored."""
        settings = Settings.model_validate({
            "workspace": {"path": "~/tachikoma", "unknown_key": "value"},
            "agent": {"extra_field": True},
        })

        assert settings.workspace.path == Path.home() / "tachikoma"

    def test_empty_dict_uses_all_defaults(self) -> None:
        """AC (R2): Empty config uses all defaults."""
        settings = Settings.model_validate({})

        assert settings.workspace.path == Path.home() / "tachikoma"
        assert settings.agent.model is None
        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_partial_config_uses_defaults_for_missing(self) -> None:
        """AC (R5): Missing sections use defaults."""
        settings = Settings.model_validate({
            "workspace": {"path": "~/custom"},
        })

        assert settings.workspace.path == Path.home() / "custom"
        assert settings.agent.model is None

    def test_invalid_path_type_raises_validation_error(self) -> None:
        """AC (R3): Invalid value type produces ValidationError."""
        with pytest.raises(ValidationError):
            Settings.model_validate({"workspace": {"path": 123}})


class TestDefaultConfigGeneration:
    """Tests for the default config file generator."""

    def test_generates_file_that_parses_to_empty_dict(self, tmp_path: Path) -> None:
        """AC (R4): Generated file has all values commented out."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        assert data == {}

    def test_generated_file_contains_field_comments(self, tmp_path: Path) -> None:
        """AC (R4): Generated file is annotated with descriptions."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "workspace" in content.lower()
        assert "agent" in content.lower()
        assert "path" in content
        assert "allowed_tools" in content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """AC (R4): Missing directory is created before writing."""
        config_path = tmp_path / "nested" / "dir" / "config.toml"
        _generate_default_config(config_path)

        assert config_path.exists()

    def test_permission_error_on_directory_exits(self, tmp_path: Path, mocker) -> None:
        """AC (R4): Permission denied on directory creation exits cleanly."""
        config_path = tmp_path / "no_access" / "config.toml"
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError)

        with pytest.raises(SystemExit):
            _generate_default_config(config_path)


class TestLoadSettings:
    """Tests for the config loader function."""

    def test_no_config_file_autogenerates_and_loads_defaults(self, tmp_path: Path) -> None:
        """AC (R4): No config file auto-generates a default and starts with defaults."""
        config_path = tmp_path / "config.toml"
        settings = load_settings(config_path)

        assert config_path.exists()
        assert settings.workspace.path == Path.home() / "tachikoma"
        assert settings.agent.model is None

    def test_empty_file_loads_all_defaults(self, tmp_path: Path) -> None:
        """AC (R2): Empty config file uses all defaults."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        settings = load_settings(config_path)

        assert settings.workspace.path == Path.home() / "tachikoma"
        assert settings.agent.model is None
        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_partial_config_merges_with_defaults(self, tmp_path: Path) -> None:
        """AC (R1): Specified values are loaded, rest use defaults."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[workspace]\npath = "~/custom"\n')

        settings = load_settings(config_path)

        assert settings.workspace.path == Path.home() / "custom"
        assert settings.agent.model is None

    def test_full_config_loads_all_values(self, tmp_path: Path) -> None:
        """AC (R1): All parameters loaded from valid config."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[workspace]\npath = "~/myworkspace"\n\n'
            '[agent]\nmodel = "claude-sonnet-4-5"\n'
            'allowed_tools = ["Read", "Write"]\n'
        )

        settings = load_settings(config_path)

        assert settings.workspace.path == Path.home() / "myworkspace"
        assert settings.agent.model == "claude-sonnet-4-5"
        assert settings.agent.allowed_tools == ["Read", "Write"]

    def test_invalid_type_exits_with_field_name(self, tmp_path: Path, capsys) -> None:
        """AC (R3): Invalid value exits with clear error naming the field."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[workspace]\npath = 123\n")

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "workspace" in err
        assert "path" in err

    def test_invalid_toml_exits_with_parse_error(self, tmp_path: Path, capsys) -> None:
        """AC (R3): Invalid TOML exits with parse error."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[workspace\n")

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "Invalid TOML" in err

    def test_permission_denied_exits_with_error(self, tmp_path: Path, mocker, capsys) -> None:
        """AC (R3): Unreadable file exits with permission error."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")
        mocker.patch("builtins.open", side_effect=PermissionError)

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "Permission denied" in err

    def test_path_is_directory_exits_with_error(self, tmp_path: Path, capsys) -> None:
        """AC (R3): Config path that is a directory exits with clear error."""
        config_path = tmp_path / "config.toml"
        config_path.mkdir()

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "not a regular file" in err

    def test_unknown_keys_silently_ignored(self, tmp_path: Path) -> None:
        """AC (R3): Unknown keys in config are ignored."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[workspace]\npath = "~/tachikoma"\nfoo = "bar"\n\n'
            '[unknown_section]\nkey = "value"\n'
        )

        settings = load_settings(config_path)

        assert settings.workspace.path == Path.home() / "tachikoma"

    def test_existing_config_not_overwritten(self, tmp_path: Path) -> None:
        """AC (R4): Existing config file is never overwritten."""
        config_path = tmp_path / "config.toml"
        original_content = '[workspace]\npath = "~/custom"\n'
        config_path.write_text(original_content)

        load_settings(config_path)

        assert config_path.read_text() == original_content

    def test_new_field_with_default_loads_from_old_config(self, tmp_path: Path) -> None:
        """AC (R5): Adding a field with default doesn't break old configs."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[workspace]\npath = "~/tachikoma"\n')

        settings = load_settings(config_path)

        # All agent defaults apply even though [agent] section is missing
        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_returns_frozen_settings(self, tmp_path: Path) -> None:
        """Settings instance is immutable after loading."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        settings = load_settings(config_path)

        with pytest.raises(ValidationError):
            settings.workspace = WorkspaceSettings(path="/other")

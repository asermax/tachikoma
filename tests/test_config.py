"""Configuration module tests.

Tests for DLT-012: Configure application parameters and secrets.
"""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from tachikoma.config import (
    LoggingSettings,
    Settings,
    SettingsManager,
    TaskSettings,
    TelegramSettings,
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
        ws = WorkspaceSettings(path=Path("~/custom"))

        assert ws.path == Path.home() / "custom"

    def test_workspace_path_expands_tilde_from_str(self) -> None:
        ws = WorkspaceSettings.model_validate({"path": "~/custom"})

        assert ws.path == Path.home() / "custom"

    def test_default_agent_model_is_none(self) -> None:
        """AC (R2): agent.model defaults to None (SDK default)."""
        settings = Settings()

        assert settings.agent.model is None

    def test_default_agent_allowed_tools(self) -> None:
        """AC (R2): agent.allowed_tools defaults to Read, Glob, Grep."""
        settings = Settings()

        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_default_session_resume_window(self) -> None:
        """AC (DLT-028): agent.session_resume_window defaults to 86400 (1 day)."""
        settings = Settings()

        assert settings.agent.session_resume_window == 86400

    def test_custom_session_resume_window(self) -> None:
        """AC (DLT-028): agent.session_resume_window can be customized."""
        settings = Settings.model_validate({
            "agent": {"session_resume_window": 3600},
        })

        assert settings.agent.session_resume_window == 3600

    def test_default_agent_env_is_empty_dict(self) -> None:
        """AC: agent.env defaults to empty dict."""
        settings = Settings()

        assert settings.agent.env == {}

    def test_agent_env_with_string_values(self) -> None:
        """AC: agent.env accepts string key-value pairs."""
        settings = Settings.model_validate({
            "agent": {"env": {"FOO": "bar", "BAZ": "qux"}},
        })

        assert settings.agent.env == {"FOO": "bar", "BAZ": "qux"}

    def test_agent_env_rejects_non_string_values(self) -> None:
        """AC: agent.env rejects non-string values with a clear error."""
        with pytest.raises(ValidationError, match="env"):
            Settings.model_validate({
                "agent": {"env": {"FOO": 42}},
            })

    def test_frozen_prevents_mutation(self) -> None:
        """Settings instances are immutable."""
        settings = Settings()

        with pytest.raises(ValidationError):
            settings.workspace = WorkspaceSettings(path=Path("/other"))

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

    def test_data_path_returns_tachikoma_subfolder(self) -> None:
        """AC (R1, DLT-023): data_path is .tachikoma under workspace path."""
        ws = WorkspaceSettings(path=Path("/workspace"))

        assert ws.data_path == Path("/workspace/.tachikoma")

    def test_invalid_path_type_raises_validation_error(self) -> None:
        """AC (R3): Invalid value type produces ValidationError."""
        with pytest.raises(ValidationError):
            Settings.model_validate({"workspace": {"path": 123}})

    def test_default_logging_level_is_info(self) -> None:
        """AC (R2, DLT-013): logging.level defaults to INFO."""
        settings = Settings()

        assert settings.logging.level == "INFO"

    def test_default_logging_console_is_false(self) -> None:
        """AC (R2, DLT-013): logging.console defaults to False."""
        settings = Settings()

        assert settings.logging.console is False

    def test_invalid_logging_level_raises_validation_error(self) -> None:
        """AC (R3, DLT-013): Invalid log level produces ValidationError."""
        with pytest.raises(ValidationError):
            LoggingSettings(level="VERBOSE")


class TestDefaultConfigGeneration:

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

    def test_generated_file_contains_logging_section(self, tmp_path: Path) -> None:
        """AC (R4, DLT-013): Generated file contains [logging] section."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "logging" in content.lower()
        assert "level" in content
        assert "console" in content

    def test_generated_file_contains_session_resume_window(self, tmp_path: Path) -> None:
        """AC (DLT-028): Generated file contains session_resume_window with int format."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "session_resume_window" in content
        # Should be formatted as int, not quoted string
        assert "session_resume_window = 86400" in content
        assert 'session_resume_window = "86400"' not in content

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
            settings.workspace = WorkspaceSettings(path=Path("/other"))

    def test_logging_level_from_config(self, tmp_path: Path) -> None:
        """AC (R1, DLT-013): Logging level loaded from config."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[logging]\nlevel = "DEBUG"\n')

        settings = load_settings(config_path)

        assert settings.logging.level == "DEBUG"

    def test_invalid_logging_level_exits_with_error(self, tmp_path: Path, capsys) -> None:
        """AC (R3, DLT-013): Invalid logging level exits with clear error."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[logging]\nlevel = "VERBOSE"\n')

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "logging" in err
        assert "level" in err

    def test_agent_env_from_config(self, tmp_path: Path) -> None:
        """AC: [agent.env] with string values loads correctly."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[agent.env]\nFOO = "bar"\nBAZ = "qux"\n'
        )

        settings = load_settings(config_path)

        assert settings.agent.env == {"FOO": "bar", "BAZ": "qux"}

    def test_agent_env_missing_defaults_to_empty_dict(self, tmp_path: Path) -> None:
        """AC: Missing [agent.env] defaults to empty dict."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[agent]\nmodel = "opus"\n')

        settings = load_settings(config_path)

        assert settings.agent.env == {}

    def test_agent_env_non_string_value_exits_with_error(self, tmp_path: Path, capsys) -> None:
        """AC: Non-string env values (e.g., FOO = 42) fail validation."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[agent.env]\nFOO = 42\n")

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "env" in err


class TestSettingsManager:
    """Tests for the SettingsManager read-write config wrapper.

    Tests for DLT-023: Bootstrap agent workspace on first run.
    """

    def test_settings_returns_frozen_snapshot(self, tmp_path: Path) -> None:
        """AC (R4.1): .settings returns a frozen Settings instance."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)

        assert isinstance(manager.settings, Settings)

    def test_update_modifies_value(self, tmp_path: Path) -> None:
        """AC (R4.1): After update + save, settings reflect the change."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        manager.update("workspace", "path", str(tmp_path / "custom"))
        manager.save()

        assert manager.settings.workspace.path == tmp_path / "custom"

    def test_update_raises_on_invalid_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)

        with pytest.raises(KeyError, match="Unknown section"):
            manager.update("nonexistent", "key", "v")

    def test_update_raises_on_invalid_key(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)

        with pytest.raises(KeyError, match="Unknown key"):
            manager.update("workspace", "nonexistent", "v")

    def test_save_persists_to_file(self, tmp_path: Path) -> None:
        """AC (R4.1): After save, the TOML file reflects the updated value."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        manager.update("workspace", "path", str(tmp_path / "persisted"))
        manager.save()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        assert data["workspace"]["path"] == str(tmp_path / "persisted")

    def test_save_reloads_frozen_settings(self, tmp_path: Path) -> None:
        """AC (R4.1): .settings before and after save return different snapshots."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        before = manager.settings
        manager.update("workspace", "path", str(tmp_path / "new"))
        manager.save()
        after = manager.settings

        assert before is not after
        assert before.workspace.path != after.workspace.path

    def test_multiple_updates_before_save(self, tmp_path: Path) -> None:
        """AC (R4.1): Batched updates are all reflected after a single save."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        manager.update("workspace", "path", str(tmp_path / "ws"))
        manager.update("agent", "model", "claude-sonnet-4-5")
        manager.save()

        assert manager.settings.workspace.path == tmp_path / "ws"
        assert manager.settings.agent.model == "claude-sonnet-4-5"

    def test_save_preserves_toml_comments(self, tmp_path: Path) -> None:
        """AC (R4.1): Config file comments are preserved after save."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('# User comment\n[workspace]\npath = "~/tachikoma"\n')

        manager = SettingsManager(config_path)
        manager.update("workspace", "path", str(tmp_path / "new"))
        manager.save()

        content = config_path.read_text()

        assert "# User comment" in content


class TestTelegramSettings:
    """Tests for TelegramSettings model (DLT-002)."""

    def test_telegram_settings_requires_both_fields(self) -> None:
        """AC (R8): TelegramSettings requires bot_token and authorized_chat_id."""
        with pytest.raises(ValidationError):
            TelegramSettings()

        with pytest.raises(ValidationError):
            TelegramSettings(bot_token="token")

        with pytest.raises(ValidationError):
            TelegramSettings(authorized_chat_id=123)

    def test_telegram_settings_valid_with_all_fields(self) -> None:
        """AC (R8): TelegramSettings validates with both fields."""
        settings = TelegramSettings(bot_token="my_token", authorized_chat_id=12345)

        assert settings.bot_token == "my_token"
        assert settings.authorized_chat_id == 12345

    def test_telegram_settings_accepts_negative_chat_id(self) -> None:
        """AC (R8): Telegram chat IDs can be negative (groups)."""
        settings = TelegramSettings(bot_token="token", authorized_chat_id=-1001234567890)

        assert settings.authorized_chat_id == -1001234567890

    def test_default_channel_is_repl(self) -> None:
        """AC (R9): Default channel is 'repl'."""
        settings = Settings()

        assert settings.channel == "repl"

    def test_default_telegram_is_none(self) -> None:
        """AC (R8): Default telegram is None (not configured)."""
        settings = Settings()

        assert settings.telegram is None

    def test_settings_with_valid_telegram_section(self) -> None:
        """AC (R8): Settings validates with a valid telegram section."""
        settings = Settings.model_validate({
            "telegram": {
                "bot_token": "my_token",
                "authorized_chat_id": 12345,
            },
        })

        assert settings.telegram is not None
        assert settings.telegram.bot_token == "my_token"
        assert settings.telegram.authorized_chat_id == 12345

    def test_settings_with_missing_telegram_field_raises_error(self, tmp_path: Path) -> None:
        """AC (R8): Partial telegram section raises ValidationError with field name."""
        with pytest.raises(ValidationError) as exc_info:
            Settings.model_validate({
                "telegram": {"bot_token": "token"},
            })

        errors = exc_info.value.errors()
        assert any("authorized_chat_id" in str(e) for e in errors)

    def test_settings_without_telegram_uses_none(self) -> None:
        """AC (R8): Missing telegram section uses None."""
        settings = Settings.model_validate({})

        assert settings.telegram is None


class TestTelegramDefaultConfig:
    """Tests for telegram section in default config generation (DLT-002)."""

    def test_generated_file_contains_telegram_section(self, tmp_path: Path) -> None:
        """AC (R8): Generated file contains [telegram] section."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "telegram" in content.lower()
        assert "bot_token" in content
        assert "authorized_chat_id" in content

    def test_generated_file_contains_channel_field(self, tmp_path: Path) -> None:
        """AC (R9): Generated file contains channel field."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "channel" in content.lower()


class TestSettingsManagerTelegram:
    """Tests for SettingsManager with telegram section (DLT-002)."""

    def test_update_telegram_section_with_union_type(self, tmp_path: Path) -> None:
        """AC (R8): update() handles optional section types (TelegramSettings | None)."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        manager.update("telegram", "bot_token", "test_token")
        manager.update("telegram", "authorized_chat_id", 12345)
        manager.save()

        assert manager.settings.telegram is not None
        assert manager.settings.telegram.bot_token == "test_token"
        assert manager.settings.telegram.authorized_chat_id == 12345

    def test_update_root_modifies_channel(self, tmp_path: Path) -> None:
        """AC (R9): update_root() modifies root-level channel field."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        manager.update_root("channel", "telegram")
        manager.reload()

        assert manager.settings.channel == "telegram"

    def test_reload_without_save(self, tmp_path: Path) -> None:
        """AC (R9): reload() updates settings without file I/O."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)
        original_content = config_path.read_text()

        manager.update_root("channel", "telegram")
        manager.reload()

        # Settings reflect the change
        assert manager.settings.channel == "telegram"
        # File was not modified
        assert config_path.read_text() == original_content

    def test_update_root_with_unknown_key_raises_error(self, tmp_path: Path) -> None:
        """AC (R9): update_root with unknown key raises KeyError."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)

        with pytest.raises(KeyError, match="Unknown root key"):
            manager.update_root("nonexistent", "value")

    def test_update_root_with_section_name_raises_error(self, tmp_path: Path) -> None:
        """AC (R9): update_root with section name raises error."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        manager = SettingsManager(config_path)

        with pytest.raises(KeyError, match="is a section"):
            manager.update_root("workspace", "value")


class TestTaskSettings:
    """Tests for TaskSettings model (DLT-010)."""

    def test_default_idle_window(self) -> None:
        """AC (DLT-010): tasks.idle_window defaults to 300 seconds."""
        settings = TaskSettings()

        assert settings.idle_window == 300

    def test_default_check_interval(self) -> None:
        """AC (DLT-010): tasks.check_interval defaults to 300 seconds."""
        settings = TaskSettings()

        assert settings.check_interval == 300

    def test_default_max_iterations(self) -> None:
        """AC (DLT-010): tasks.max_iterations defaults to 10."""
        settings = TaskSettings()

        assert settings.max_iterations == 10

    def test_default_max_concurrent_background(self) -> None:
        """AC (DLT-010): tasks.max_concurrent_background defaults to 3."""
        settings = TaskSettings()

        assert settings.max_concurrent_background == 3

    def test_default_timezone(self) -> None:
        """AC (DLT-010): tasks.timezone defaults to empty string (system tz)."""
        settings = TaskSettings()

        assert settings.timezone == ""

    def test_settings_has_tasks_with_defaults(self) -> None:
        """AC (DLT-010): Settings has tasks field with default TaskSettings."""
        settings = Settings()

        assert settings.tasks.idle_window == 300
        assert settings.tasks.max_iterations == 10

    def test_tasks_settings_from_config(self, tmp_path: Path) -> None:
        """AC (DLT-010): TaskSettings loaded from config."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[tasks]\nidle_window = 600\nmax_iterations = 20\ntimezone = "America/New_York"\n'
        )

        settings = load_settings(config_path)

        assert settings.tasks.idle_window == 600
        assert settings.tasks.max_iterations == 20
        assert settings.tasks.timezone == "America/New_York"

    def test_tasks_settings_extra_fields_ignored(self) -> None:
        """AC (DLT-010): Unknown fields in [tasks] are ignored."""
        settings = Settings.model_validate({
            "tasks": {"idle_window": 120, "unknown_field": "value"},
        })

        assert settings.tasks.idle_window == 120


class TestTaskSettingsDefaultConfig:
    """Tests for tasks section in default config generation (DLT-010)."""

    def test_generated_file_contains_tasks_section(self, tmp_path: Path) -> None:
        """AC (DLT-010): Generated file contains [tasks] section."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "[tasks]" in content
        assert "idle_window" in content
        assert "check_interval" in content
        assert "max_iterations" in content
        assert "max_concurrent_background" in content
        assert "timezone" in content

    def test_generated_tasks_section_uses_int_format(self, tmp_path: Path) -> None:
        """AC (DLT-010): Tasks int fields are formatted as ints, not strings."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "idle_window = 300" in content
        assert 'idle_window = "300"' not in content
        assert "max_iterations = 10" in content

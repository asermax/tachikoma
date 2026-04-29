"""Configuration module tests.

Tests for DLT-012: Configure application parameters and secrets.
"""

import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tachikoma.agent_defaults import SYSTEM_DISALLOWED_TOOLS
from tachikoma.config import (
    BufferSettings,
    LoggingSettings,
    SendFileSettings,
    Settings,
    SettingsManager,
    TaskSettings,
    TelegramSettings,
    WorkspaceSettings,
    _detect_system_timezone,
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

    def test_default_agent_model_is_opus(self) -> None:
        """AC (R2): agent.model defaults to 'opus'."""
        settings = Settings()

        assert settings.agent.model == "opus"

    def test_default_agent_allowed_tools(self) -> None:
        """AC (R2): agent.allowed_tools defaults to Read, Glob, Grep."""
        settings = Settings()

        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_default_agent_disallowed_tools(self) -> None:
        """AC (AC1): agent.disallowed_tools defaults to user defaults + system tools."""
        settings = Settings()

        result = settings.agent.disallowed_tools
        # User default comes first, then system tools in frozenset iteration order
        assert result[0] == "AskUserQuestion"
        assert set(result[1:]) == SYSTEM_DISALLOWED_TOOLS
        assert len(result) == 1 + len(SYSTEM_DISALLOWED_TOOLS)

    def test_default_session_resume_window(self) -> None:
        """AC (DLT-028): agent.session_resume_window defaults to 86400 (1 day)."""
        settings = Settings()

        assert settings.agent.session_resume_window == 86400

    def test_custom_session_resume_window(self) -> None:
        """AC (DLT-028): agent.session_resume_window can be customized."""
        settings = Settings.model_validate(
            {
                "agent": {"session_resume_window": 3600},
            }
        )

        assert settings.agent.session_resume_window == 3600

    def test_default_session_idle_timeout(self) -> None:
        """AC (DLT-036): agent.session_idle_timeout defaults to 900 (15 min)."""
        settings = Settings()

        assert settings.agent.session_idle_timeout == 900

    def test_session_idle_timeout_zero_accepted(self) -> None:
        """AC (DLT-036): agent.session_idle_timeout = 0 disables idle close."""
        settings = Settings.model_validate(
            {
                "agent": {"session_idle_timeout": 0},
            }
        )

        assert settings.agent.session_idle_timeout == 0

    def test_session_idle_timeout_from_toml(self) -> None:
        """AC (DLT-036): agent.session_idle_timeout loads from TOML."""
        settings = Settings.model_validate(
            {
                "agent": {"session_idle_timeout": 600},
            }
        )

        assert settings.agent.session_idle_timeout == 600

    def test_default_agent_env_is_empty_dict(self) -> None:
        """AC: agent.env defaults to empty dict."""
        settings = Settings()

        assert settings.agent.env == {}

    def test_agent_env_with_string_values(self) -> None:
        """AC: agent.env accepts string key-value pairs."""
        settings = Settings.model_validate(
            {
                "agent": {"env": {"FOO": "bar", "BAZ": "qux"}},
            }
        )

        assert settings.agent.env == {"FOO": "bar", "BAZ": "qux"}

    def test_agent_env_rejects_non_string_values(self) -> None:
        """AC: agent.env rejects non-string values with a clear error."""
        with pytest.raises(ValidationError, match="env"):
            Settings.model_validate(
                {
                    "agent": {"env": {"FOO": 42}},
                }
            )

    def test_frozen_prevents_mutation(self) -> None:
        """Settings instances are immutable."""
        settings = Settings()

        with pytest.raises(ValidationError):
            settings.workspace = WorkspaceSettings(path=Path("/other"))

    def test_extra_fields_ignored(self) -> None:
        """AC (R3): Unknown keys are silently ignored."""
        settings = Settings.model_validate(
            {
                "workspace": {"path": "~/tachikoma", "unknown_key": "value"},
                "agent": {"extra_field": True},
            }
        )

        assert settings.workspace.path == Path.home() / "tachikoma"

    def test_empty_dict_uses_all_defaults(self) -> None:
        """AC (R2): Empty config uses all defaults."""
        settings = Settings.model_validate({})

        assert settings.workspace.path == Path.home() / "tachikoma"
        assert settings.agent.model == "opus"
        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_partial_config_uses_defaults_for_missing(self) -> None:
        """AC (R5): Missing sections use defaults."""
        settings = Settings.model_validate(
            {
                "workspace": {"path": "~/custom"},
            }
        )

        assert settings.workspace.path == Path.home() / "custom"
        assert settings.agent.model == "opus"

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


class TestSystemDisallowedTools:
    """Tests for system-level disallowed tools merge behavior (DLT-087)."""

    def test_system_tools_merged_with_user_config(self) -> None:
        """AC (R2): User-configured tools preserved, system tools appended."""
        settings = Settings.model_validate(
            {"agent": {"disallowed_tools": ["AskUserQuestion", "WebSearch"]}}
        )

        result = settings.agent.disallowed_tools
        assert result[:2] == ["AskUserQuestion", "WebSearch"]
        assert set(result[2:]) == SYSTEM_DISALLOWED_TOOLS

    def test_system_tools_present_with_empty_list(self) -> None:
        """AC (R2): System tools present even when user sets empty list."""
        settings = Settings.model_validate({"agent": {"disallowed_tools": []}})

        result = settings.agent.disallowed_tools
        assert set(result) == SYSTEM_DISALLOWED_TOOLS

    def test_system_tools_no_duplicate_when_user_includes(self) -> None:
        """AC (R2): No duplicate when user already includes a system tool."""
        settings = Settings.model_validate(
            {"agent": {"disallowed_tools": ["AskUserQuestion", "Skill"]}}
        )

        result = settings.agent.disallowed_tools
        assert result[:2] == ["AskUserQuestion", "Skill"]
        assert set(result[2:]) == (SYSTEM_DISALLOWED_TOOLS - {"Skill"})

    def test_system_tools_user_order_preserved(self) -> None:
        """AC (R3): User entry order preserved, system tools appended."""
        settings = Settings.model_validate(
            {"agent": {"disallowed_tools": ["WebSearch", "AskUserQuestion"]}}
        )

        result = settings.agent.disallowed_tools
        assert result[:2] == ["WebSearch", "AskUserQuestion"]
        assert set(result[2:]) == SYSTEM_DISALLOWED_TOOLS

    def test_constant_contains_skill(self) -> None:
        """SYSTEM_DISALLOWED_TOOLS contains 'Skill'."""
        assert "Skill" in SYSTEM_DISALLOWED_TOOLS

    def test_constant_contains_audited_tools(self) -> None:
        """AC (R1): SYSTEM_DISALLOWED_TOOLS contains all audited tools."""
        expected = {
            "Skill",
            "CronCreate",
            "CronDelete",
            "CronList",
            "RemoteTrigger",
            "ScheduleWakeup",
            "PushNotification",
        }
        assert expected == SYSTEM_DISALLOWED_TOOLS


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

    def test_generated_file_contains_disallowed_tools(self, tmp_path: Path) -> None:
        """AC (AC3): Generated file contains disallowed_tools with user defaults."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "disallowed_tools" in content
        assert '"AskUserQuestion"' in content

    def test_explicit_disallowed_tools_override_keeps_system_tools(self, tmp_path: Path) -> None:
        """AC (AC3): Explicit override replaces user defaults but system tools persist."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[agent]\ndisallowed_tools = ["AskUserQuestion"]\n')

        settings = load_settings(config_file)

        result = settings.agent.disallowed_tools
        assert result[0] == "AskUserQuestion"
        assert set(result[1:]) == SYSTEM_DISALLOWED_TOOLS

    def test_generated_file_contains_session_resume_window(self, tmp_path: Path) -> None:
        """AC (DLT-028): Generated file contains session_resume_window with int format."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "session_resume_window" in content
        # Should be formatted as int, not quoted string
        assert "session_resume_window = 86400" in content
        assert 'session_resume_window = "86400"' not in content

    def test_generated_file_contains_session_idle_timeout(self, tmp_path: Path) -> None:
        """AC (DLT-036): Generated file contains session_idle_timeout with int format."""
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "session_idle_timeout" in content
        # Should be formatted as int, not quoted string
        assert "session_idle_timeout = 900" in content
        assert 'session_idle_timeout = "900"' not in content

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
        assert settings.agent.model == "opus"

    def test_empty_file_loads_all_defaults(self, tmp_path: Path) -> None:
        """AC (R2): Empty config file uses all defaults."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("")

        settings = load_settings(config_path)

        assert settings.workspace.path == Path.home() / "tachikoma"
        assert settings.agent.model == "opus"
        assert settings.agent.allowed_tools == ["Read", "Glob", "Grep"]

    def test_partial_config_merges_with_defaults(self, tmp_path: Path) -> None:
        """AC (R1): Specified values are loaded, rest use defaults."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[workspace]\npath = "~/custom"\n')

        settings = load_settings(config_path)

        assert settings.workspace.path == Path.home() / "custom"
        assert settings.agent.model == "opus"

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
            '[workspace]\npath = "~/tachikoma"\nfoo = "bar"\n\n[unknown_section]\nkey = "value"\n'
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
        config_path.write_text('[agent.env]\nFOO = "bar"\nBAZ = "qux"\n')

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
        settings = Settings.model_validate(
            {
                "telegram": {
                    "bot_token": "my_token",
                    "authorized_chat_id": 12345,
                },
            }
        )

        assert settings.telegram is not None
        assert settings.telegram.bot_token == "my_token"
        assert settings.telegram.authorized_chat_id == 12345

    def test_settings_with_missing_telegram_field_raises_error(self, tmp_path: Path) -> None:
        """AC (R8): Partial telegram section raises ValidationError with field name."""
        with pytest.raises(ValidationError) as exc_info:
            Settings.model_validate(
                {
                    "telegram": {"bot_token": "token"},
                }
            )

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
        """AC (R5): tasks.timezone resolves to a non-empty valid IANA key."""
        settings = TaskSettings()

        assert settings.timezone != ""
        assert len(settings.timezone) > 0

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
        settings = Settings.model_validate(
            {
                "tasks": {"idle_window": 120, "unknown_field": "value"},
            }
        )

        assert settings.tasks.idle_window == 120

    def test_timezone_empty_resolves_to_system_tz(self) -> None:
        """AC (R5): Empty timezone resolves to a non-empty valid IANA key."""
        settings = TaskSettings()

        assert settings.timezone != ""
        # Verify it's a valid IANA key
        ZoneInfo(settings.timezone)  # Should not raise

    def test_timezone_explicit_valid_preserved(self) -> None:
        """AC (R5): Explicit valid timezone is preserved."""
        settings = TaskSettings(timezone="America/New_York")

        assert settings.timezone == "America/New_York"

    def test_timezone_invalid_raises_validation_error(self) -> None:
        """AC (R5): Invalid timezone raises ValidationError."""
        with pytest.raises(ValidationError, match="not a valid IANA timezone"):
            TaskSettings(timezone="Fake/Timezone")

    def test_detect_system_timezone_resolves_symlink(self, mocker) -> None:
        """AC (R5): _detect_system_timezone extracts IANA name from symlink."""
        mock_path = mocker.patch("tachikoma.config.Path")
        mock_localtime = mocker.MagicMock()
        mock_localtime.resolve.return_value = "/usr/share/zoneinfo/America/Buenos_Aires"
        mock_path.return_value = mock_localtime

        result = _detect_system_timezone()

        assert result == "America/Buenos_Aires"

    def test_detect_system_timezone_fallback_utc(self, mocker) -> None:
        """AC (R5): _detect_system_timezone falls back to UTC on failure."""
        mock_path = mocker.patch("tachikoma.config.Path")
        mock_localtime = mocker.MagicMock()
        mock_localtime.resolve.side_effect = OSError("no symlink")
        mock_path.return_value = mock_localtime

        result = _detect_system_timezone()

        assert result == "UTC"


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


class TestSendFileSettings:
    """Tests for SendFileSettings model (DLT-140)."""

    def test_default_extra_roots_is_empty(self) -> None:
        settings = SendFileSettings()

        assert settings.extra_roots == []

    def test_tilde_expanded(self) -> None:
        settings = SendFileSettings(extra_roots=["~/exports"])

        assert settings.extra_roots == [Path.home() / "exports"]

    def test_tilde_expanded_from_path(self) -> None:
        settings = SendFileSettings(extra_roots=[Path("~/exports")])

        assert settings.extra_roots == [Path.home() / "exports"]

    def test_absolute_path_accepted(self) -> None:
        settings = SendFileSettings(extra_roots=["/srv/artifacts"])

        assert settings.extra_roots == [Path("/srv/artifacts")]

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_roots entries must be absolute"):
            SendFileSettings(extra_roots=["exports"])

    def test_nonexistent_path_accepted(self) -> None:
        """Extra roots need not exist at load time."""
        settings = SendFileSettings(extra_roots=["/srv/doesnotexist"])

        assert settings.extra_roots == [Path("/srv/doesnotexist")]

    def test_explicit_empty_list_accepted(self) -> None:
        settings = SendFileSettings(extra_roots=[])

        assert settings.extra_roots == []

    def test_missing_section_defaults_in_telegram(self) -> None:
        """Missing [telegram.send_file] falls back to empty defaults."""
        settings = TelegramSettings(bot_token="t", authorized_chat_id=1)

        assert settings.send_file.extra_roots == []

    def test_nested_in_telegram_from_dict(self) -> None:
        settings = TelegramSettings(
            bot_token="t",
            authorized_chat_id=1,
            send_file={"extra_roots": ["/tmp/x"]},
        )

        assert settings.send_file.extra_roots == [Path("/tmp/x")]

    def test_loads_from_toml(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[telegram]\nbot_token = "t"\nauthorized_chat_id = 1\n'
            '\n[telegram.send_file]\nextra_roots = ["/tmp/a", "~/b"]\n'
        )

        settings = load_settings(config_path)

        assert settings.telegram is not None
        assert settings.telegram.send_file.extra_roots == [
            Path("/tmp/a"),
            Path.home() / "b",
        ]

    def test_relative_rejected_via_load_settings(self, tmp_path: Path, capsys) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[telegram]\nbot_token = "t"\nauthorized_chat_id = 1\n'
            '\n[telegram.send_file]\nextra_roots = ["exports"]\n'
        )

        with pytest.raises(SystemExit):
            load_settings(config_path)

        err = capsys.readouterr().err
        assert "exports" in err

    def test_generated_default_config_contains_send_file_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "[telegram.send_file]" in content
        assert "extra_roots" in content

    def test_generated_default_config_loads_to_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        settings = load_settings(config_path)

        assert settings.telegram is None
        assert settings.channel == "repl"


class TestBufferSettings:
    """Tests for BufferSettings model (DLT-112)."""

    def test_default_urgent_timing(self) -> None:
        settings = BufferSettings()

        assert settings.urgent.idle_window_seconds == 30
        assert settings.urgent.max_hold_seconds == 120

    def test_default_normal_timing(self) -> None:
        settings = BufferSettings()

        assert settings.normal.idle_window_seconds == 120
        assert settings.normal.max_hold_seconds == 900

    def test_default_low_timing(self) -> None:
        settings = BufferSettings()

        assert settings.low.idle_window_seconds == 300
        assert settings.low.max_hold_seconds is None

    def test_settings_has_buffer_with_defaults(self) -> None:
        settings = Settings()

        assert settings.buffer.urgent.idle_window_seconds == 30
        assert settings.buffer.normal.idle_window_seconds == 120
        assert settings.buffer.low.max_hold_seconds is None

    def test_buffer_from_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[buffer.urgent]\nidle_window_seconds = 10\nmax_hold_seconds = 60\n"
            "[buffer.normal]\nidle_window_seconds = 60\nmax_hold_seconds = 300\n"
        )

        settings = load_settings(config_path)

        assert settings.buffer.urgent.idle_window_seconds == 10
        assert settings.buffer.urgent.max_hold_seconds == 60
        assert settings.buffer.normal.idle_window_seconds == 60

    def test_generated_file_contains_buffer_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        _generate_default_config(config_path)

        content = config_path.read_text()

        assert "[buffer.urgent]" in content
        assert "[buffer.normal]" in content
        assert "[buffer.low]" in content
        assert "idle_window_seconds" in content


class TestPluginsSettings:
    """Tests for Settings.plugins field (DLT-048, Batch 1).

    Covers AC-PSD-1, AC-PSD-2, AC-PSD-6, AC-PSD-9, AC-PSD-14.
    """

    def test_default_plugins_is_empty_dict(self) -> None:
        """Plugins defaults to an empty dict."""
        settings = Settings()

        assert settings.plugins == {}

    def test_git_plugin_from_config(self, tmp_path: Path) -> None:
        """AC-PSD-1: A [plugins.code-review] git source is parsed correctly."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins.code-review]\n'
            'git = "https://github.com/owner/repo.git"\n'
            'subdir = "plugin"\n'
            'ref = "v1.0.0"\n'
        )

        settings = load_settings(config_path)

        assert "code-review" in settings.plugins
        plugin = settings.plugins["code-review"]
        from tachikoma.plugins.sources import GitPluginSource

        assert isinstance(plugin, GitPluginSource)
        assert plugin.git == "https://github.com/owner/repo.git"
        assert plugin.subdir == "plugin"
        assert plugin.ref == "v1.0.0"

    def test_local_plugin_from_config(self, tmp_path: Path) -> None:
        """AC-PSD-2: A [plugins.dev-plugin] local source is parsed correctly."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins.dev-plugin]\n'
            'path = "/home/user/dev/my-plugin"\n'
        )

        settings = load_settings(config_path)

        assert "dev-plugin" in settings.plugins
        from tachikoma.plugins.sources import LocalPluginSource

        assert isinstance(settings.plugins["dev-plugin"], LocalPluginSource)
        assert settings.plugins["dev-plugin"].path == Path("/home/user/dev/my-plugin")

    def test_url_plugin_from_config_no_subdir(self, tmp_path: Path) -> None:
        """AC-PSD-14: A URL source with no subdir parses correctly."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins.foo]\n'
            'url = "https://example.com/plugin.tar.gz"\n'
        )

        settings = load_settings(config_path)

        assert "foo" in settings.plugins
        from tachikoma.plugins.sources import UrlPluginSource

        plugin = settings.plugins["foo"]
        assert isinstance(plugin, UrlPluginSource)
        assert plugin.url == "https://example.com/plugin.tar.gz"
        assert plugin.subdir is None

    def test_invalid_alias_rejected(self, tmp_path: Path) -> None:
        """AC-PSD-6: An alias with reserved characters is rejected."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins."code:review"]\n'
            'path = "/home/user/plugin"\n'
        )

        with pytest.raises(SystemExit):
            load_settings(config_path)

    def test_uppercase_alias_rejected(self, tmp_path: Path) -> None:
        """AC-PSD-6: An uppercase alias is rejected."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins.CodeReview]\n'
            'path = "/home/user/plugin"\n'
        )

        with pytest.raises(SystemExit):
            load_settings(config_path)

    def test_duplicate_alias_rejected_by_toml(self, tmp_path: Path) -> None:
        """AC-PSD-9: TOML duplicate keys are rejected by stdlib parser."""
        toml_text = (
            '[plugins.my-plugin]\n'
            'path = "/a"\n'
            '[plugins.my-plugin]\n'
            'path = "/b"\n'
        )
        with pytest.raises(tomllib.TOMLDecodeError):
            tomllib.loads(toml_text)

    def test_multiple_plugins_from_config(self, tmp_path: Path) -> None:
        """Multiple plugin entries are all parsed."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins.alpha]\n'
            'git = "https://github.com/owner/alpha.git"\n'
            'ref = "v1.0.0"\n\n'
            '[plugins.beta]\n'
            'path = "/home/user/beta"\n'
        )

        settings = load_settings(config_path)

        assert len(settings.plugins) == 2
        assert "alpha" in settings.plugins
        assert "beta" in settings.plugins

    def test_plugins_default_when_section_absent(self, tmp_path: Path) -> None:
        """Plugins is empty dict when no [plugins] section exists."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[workspace]\npath = "~/tachikoma"\n')

        settings = load_settings(config_path)

        assert settings.plugins == {}

    def test_gh_shorthand_expanded_in_config(self, tmp_path: Path) -> None:
        """AC-PSD-10: gh: shorthand is expanded during config load."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[plugins.code-review]\n'
            'git = "gh:owner/repo"\n'
            'ref = "v1.0.0"\n'
        )

        settings = load_settings(config_path)

        plugin = settings.plugins["code-review"]
        from tachikoma.plugins.sources import GitPluginSource

        assert isinstance(plugin, GitPluginSource)
        assert plugin.git == "https://github.com/owner/repo.git"

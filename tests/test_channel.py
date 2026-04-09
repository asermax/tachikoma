"""Channel protocol tests."""

from pathlib import Path
from unittest.mock import MagicMock

import frontmatter

from tachikoma.channel import Channel
from tachikoma.repl import Repl
from tachikoma.telegram import TelegramChannel


def _make_telegram_settings() -> MagicMock:
    """Create mock TelegramSettings."""
    settings = MagicMock()
    settings.bot_token = "123456:ABCdef"
    settings.authorized_chat_id = 123
    settings.push_notifications = False
    return settings


TELEGRAM_WORKSPACE = Path("/tmp/test-workspace")


class TestChannelDefaults:
    """Tests for Channel protocol default implementations."""

    def test_repl_inherits_default_mcp_servers(self) -> None:
        """Repl inherits empty dict for get_mcp_servers()."""
        repl = Repl(history_path=Path("/tmp/test"))

        assert repl.get_mcp_servers() == {}

    def test_repl_inherits_default_skill_sources(self) -> None:
        """Repl inherits empty list for get_skill_sources()."""
        repl = Repl(history_path=Path("/tmp/test"))

        assert repl.get_skill_sources() == []

    def test_repl_is_channel_instance(self) -> None:
        """Repl is recognized as a Channel instance."""
        repl = Repl(history_path=Path("/tmp/test"))

        assert isinstance(repl, Channel)

    def test_telegram_channel_is_channel_instance(self) -> None:
        """TelegramChannel is recognized as a Channel instance."""
        channel = TelegramChannel(_make_telegram_settings(), workspace_path=TELEGRAM_WORKSPACE)

        assert isinstance(channel, Channel)

    def test_telegram_channel_get_mcp_servers_returns_dict(self) -> None:
        """TelegramChannel.get_mcp_servers() returns a dict."""
        channel = TelegramChannel(_make_telegram_settings(), workspace_path=TELEGRAM_WORKSPACE)

        assert isinstance(channel.get_mcp_servers(), dict)

    def test_telegram_channel_get_skill_sources_returns_list(self) -> None:
        """TelegramChannel.get_skill_sources() returns a list."""
        channel = TelegramChannel(_make_telegram_settings(), workspace_path=TELEGRAM_WORKSPACE)

        assert isinstance(channel.get_skill_sources(), list)

    def test_telegram_channel_get_mcp_servers_contains_send_file(self) -> None:
        """TelegramChannel.get_mcp_servers() returns a dict with 'send-file' key."""
        channel = TelegramChannel(_make_telegram_settings(), workspace_path=TELEGRAM_WORKSPACE)

        servers = channel.get_mcp_servers()
        assert "send-file" in servers
        assert servers["send-file"]["name"] == "send-file"

    def test_telegram_channel_get_skill_sources_contains_skill_path(self) -> None:
        """TelegramChannel.get_skill_sources() returns path to skill directory."""
        channel = TelegramChannel(_make_telegram_settings(), workspace_path=TELEGRAM_WORKSPACE)

        sources = channel.get_skill_sources()
        assert len(sources) == 1
        assert sources[0].name == "skill"


class TestTelegramSkillContent:
    """Tests for the telegram tools skill (SKILL.md)."""

    def _skill_path(self) -> Path:
        return (
            Path(__file__).parent.parent
            / "src"
            / "tachikoma"
            / "telegram"
            / "skill"
            / "SKILL.md"
        )

    def test_skill_md_has_required_frontmatter(self) -> None:
        """Skill has description in frontmatter."""
        post = frontmatter.load(str(self._skill_path()))

        assert "description" in post.metadata
        assert "send" in post.metadata["description"].lower()
        assert "file" in post.metadata["description"].lower()

    def test_skill_md_documents_parameters(self) -> None:
        """Skill documents file_path and caption parameters."""
        post = frontmatter.load(str(self._skill_path()))

        body = post.content.lower()
        assert "file_path" in body
        assert "caption" in body

    def test_skill_md_documents_media_types(self) -> None:
        """Skill documents supported media types."""
        post = frontmatter.load(str(self._skill_path()))

        body = post.content.lower()
        assert "image" in body
        assert "audio" in body
        assert "video" in body
        assert "document" in body

    def test_skill_md_documents_workspace_constraint(self) -> None:
        """Skill documents the workspace file requirement."""
        post = frontmatter.load(str(self._skill_path()))

        body = post.content.lower()
        assert "workspace" in body

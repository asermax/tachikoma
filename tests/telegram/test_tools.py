"""Tests for the send_file tool server (telegram/tools.py).

Tests for DLT-063: Send files and media to users.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramAPIError

from tachikoma.telegram.tools import (
    create_send_file_server,
    detect_media_type,
    handle_send_file,
    validate_file_path,
)


class TestDetectMediaType:
    """Tests for media type detection from file extensions."""

    def test_png_detected_as_photo(self) -> None:
        assert detect_media_type(Path("image.png")) == "photo"

    def test_jpg_detected_as_photo(self) -> None:
        assert detect_media_type(Path("image.jpg")) == "photo"

    def test_jpeg_detected_as_photo(self) -> None:
        assert detect_media_type(Path("image.jpeg")) == "photo"

    def test_gif_detected_as_photo(self) -> None:
        assert detect_media_type(Path("image.gif")) == "photo"

    def test_webp_detected_as_photo(self) -> None:
        assert detect_media_type(Path("image.webp")) == "photo"

    def test_mp3_detected_as_audio(self) -> None:
        assert detect_media_type(Path("audio.mp3")) == "audio"

    def test_ogg_detected_as_audio(self) -> None:
        assert detect_media_type(Path("audio.ogg")) == "audio"

    def test_wav_detected_as_audio(self) -> None:
        assert detect_media_type(Path("audio.wav")) == "audio"

    def test_flac_detected_as_audio(self) -> None:
        assert detect_media_type(Path("audio.flac")) == "audio"

    def test_mp4_detected_as_video(self) -> None:
        assert detect_media_type(Path("video.mp4")) == "video"

    def test_avi_detected_as_video(self) -> None:
        assert detect_media_type(Path("video.avi")) == "video"

    def test_mov_detected_as_video(self) -> None:
        assert detect_media_type(Path("video.mov")) == "video"

    def test_webm_detected_as_video(self) -> None:
        assert detect_media_type(Path("video.webm")) == "video"

    def test_unknown_extension_falls_back_to_document(self) -> None:
        assert detect_media_type(Path("file.pdf")) == "document"

    def test_no_extension_falls_back_to_document(self) -> None:
        assert detect_media_type(Path("README")) == "document"

    def test_py_file_falls_back_to_document(self) -> None:
        assert detect_media_type(Path("script.py")) == "document"

    def test_uppercase_extension_detected(self) -> None:
        assert detect_media_type(Path("image.PNG")) == "photo"


class TestValidateFilePath:
    """Tests for file path validation."""

    def test_absolute_path_within_workspace(self, tmp_path: Path) -> None:
        """Absolute path within workspace resolves correctly."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        result = validate_file_path(str(test_file), tmp_path)

        assert result == test_file.resolve()

    def test_relative_path_resolved_against_workspace(self, tmp_path: Path) -> None:
        """Relative path resolved against workspace root."""
        test_file = tmp_path / "output" / "report.pdf"
        test_file.parent.mkdir()
        test_file.write_text("content")

        result = validate_file_path("output/report.pdf", tmp_path)

        assert result == test_file.resolve()

    def test_nonexistent_file_raises_value_error(self, tmp_path: Path) -> None:
        """Non-existent file raises ValueError."""
        with pytest.raises(ValueError, match="File not found"):
            validate_file_path("missing.txt", tmp_path)

    def test_path_outside_workspace_raises_value_error(self, tmp_path: Path) -> None:
        """Path outside workspace raises ValueError."""
        with pytest.raises(ValueError, match="File must be within the workspace"):
            validate_file_path("/etc/passwd", tmp_path)

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Path traversal via .. components is blocked."""
        # Create a file outside the workspace to test workspace boundary
        outside_dir = tmp_path.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        outside_file = outside_dir / "secret.txt"
        outside_file.write_text("secret")

        # Attempt traversal from workspace to outside
        traversal_path = "../outside/secret.txt"
        with pytest.raises(ValueError, match="File must be within the workspace"):
            validate_file_path(traversal_path, tmp_path)


class TestHandleSendFile:
    """Tests for the handle_send_file handler."""

    async def test_sends_photo_for_png(self, tmp_path: Path) -> None:
        """PNG file is sent using send_photo."""
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"\x89PNG\r\n")

        bot = MagicMock()
        bot.send_photo = AsyncMock()

        result = await handle_send_file(str(test_file), None, bot, 123, tmp_path)

        bot.send_photo.assert_called_once()
        assert result["content"][0]["text"].startswith("File sent:")
        assert "image.png" in result["content"][0]["text"]

    async def test_sends_document_for_pdf(self, tmp_path: Path) -> None:
        """PDF file is sent using send_document."""
        test_file = tmp_path / "report.pdf"
        test_file.write_bytes(b"%PDF-1.4")

        bot = MagicMock()
        bot.send_document = AsyncMock()

        result = await handle_send_file(str(test_file), None, bot, 123, tmp_path)

        bot.send_document.assert_called_once()
        assert "report.pdf" in result["content"][0]["text"]

    async def test_sends_audio_for_mp3(self, tmp_path: Path) -> None:
        """MP3 file is sent using send_audio."""
        test_file = tmp_path / "song.mp3"
        test_file.write_bytes(b"ID3")

        bot = MagicMock()
        bot.send_audio = AsyncMock()

        await handle_send_file(str(test_file), None, bot, 123, tmp_path)

        bot.send_audio.assert_called_once()

    async def test_sends_video_for_mp4(self, tmp_path: Path) -> None:
        """MP4 file is sent using send_video."""
        test_file = tmp_path / "clip.mp4"
        test_file.write_bytes(b"\x00\x00\x00\x20ftypmp42")

        bot = MagicMock()
        bot.send_video = AsyncMock()

        await handle_send_file(str(test_file), None, bot, 123, tmp_path)

        bot.send_video.assert_called_once()

    async def test_returns_error_for_missing_file(self, tmp_path: Path) -> None:
        """Missing file returns is_error response."""
        bot = MagicMock()

        result = await handle_send_file("missing.png", None, bot, 123, tmp_path)

        assert result["is_error"] is True
        assert "File not found" in result["content"][0]["text"]

    async def test_returns_error_for_path_outside_workspace(self, tmp_path: Path) -> None:
        """Path outside workspace returns is_error response."""
        bot = MagicMock()

        result = await handle_send_file("/etc/passwd", None, bot, 123, tmp_path)

        assert result["is_error"] is True
        assert "workspace" in result["content"][0]["text"].lower()

    async def test_returns_error_on_telegram_api_failure(self, tmp_path: Path) -> None:
        """Telegram API error is returned as is_error response."""
        test_file = tmp_path / "big.png"
        test_file.write_bytes(b"\x89PNG")

        bot = MagicMock()
        bot.send_photo = AsyncMock(
            side_effect=TelegramAPIError(
                method="send_photo", message="File too large"
            ),
        )

        result = await handle_send_file(str(test_file), None, bot, 123, tmp_path)

        assert result["is_error"] is True
        assert "File too large" in result["content"][0]["text"]

    async def test_caption_passed_through(self, tmp_path: Path) -> None:
        """Caption is passed to the bot send method."""
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"\x89PNG")

        bot = MagicMock()
        bot.send_photo = AsyncMock()

        await handle_send_file(str(test_file), "A nice image", bot, 123, tmp_path)

        call_kwargs = bot.send_photo.call_args.kwargs
        assert call_kwargs["caption"] == "A nice image"

    async def test_no_caption_passes_none(self, tmp_path: Path) -> None:
        """No caption passes None to the bot send method."""
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"\x89PNG")

        bot = MagicMock()
        bot.send_photo = AsyncMock()

        await handle_send_file(str(test_file), None, bot, 123, tmp_path)

        call_kwargs = bot.send_photo.call_args.kwargs
        assert call_kwargs["caption"] is None


class TestCreateSendFileServer:
    """Tests for the send_file tool server factory."""

    def test_factory_returns_server_config(self) -> None:
        """Factory returns a dict with expected keys."""
        bot = MagicMock()
        workspace = Path("/tmp/workspace")

        server = create_send_file_server(bot, 123, workspace)

        # McpSdkServerConfig is a TypedDict with 'name', 'type', 'instance'
        assert server["name"] == "send-file"
        assert server["type"] == "sdk"

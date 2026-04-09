"""Tests for the media module.

Tests for DLT-035: Telegram media support.
"""

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramAPIError
from pytest_mock import MockerFixture

from tachikoma.media import (
    MEDIA_DESCRIPTORS,
    MediaTooLargeError,
    _format_file_size,
    build_description,
    download_media,
    generate_media_filename,
    media_hook,
    resolve_media,
)

# --- Helpers ---


def _make_photo(
    file_id: str = "photo_123",
    width: int = 1280,
    height: int = 720,
    file_size: int = 250_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.width = width
    media.height = height
    media.file_size = file_size
    media.file_name = None
    return media


def _make_voice(
    file_id: str = "voice_123",
    duration: int = 12,
    mime_type: str = "audio/ogg",
    file_size: int = 50_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.duration = duration
    media.mime_type = mime_type
    media.file_size = file_size
    media.file_name = None
    return media


def _make_audio(
    file_id: str = "audio_123",
    duration: int = 180,
    title: str | None = "Test Song",
    performer: str | None = "Test Artist",
    file_name: str | None = None,
    mime_type: str | None = "audio/mpeg",
    file_size: int = 3_000_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.duration = duration
    media.title = title
    media.performer = performer
    media.file_name = file_name
    media.mime_type = mime_type
    media.file_size = file_size
    return media


def _make_document(
    file_id: str = "doc_123",
    file_name: str | None = "report.pdf",
    mime_type: str | None = "application/pdf",
    file_size: int = 1_500_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.file_name = file_name
    media.mime_type = mime_type
    media.file_size = file_size
    return media


def _make_sticker(
    file_id: str = "sticker_123",
    emoji: str | None = "😊",
    is_animated: bool = False,
    is_video: bool = False,
    file_size: int = 100_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.emoji = emoji
    media.is_animated = is_animated
    media.is_video = is_video
    media.file_size = file_size
    media.file_name = None
    return media


def _make_video(
    file_id: str = "video_123",
    duration: int = 60,
    width: int = 1920,
    height: int = 1080,
    file_name: str | None = None,
    file_size: int = 10_000_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.duration = duration
    media.width = width
    media.height = height
    media.file_name = file_name
    media.file_size = file_size
    return media


def _make_video_note(
    file_id: str = "vnote_123",
    duration: int = 30,
    length: int = 240,
    file_size: int = 2_000_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.duration = duration
    media.length = length
    media.file_size = file_size
    media.file_name = None
    return media


def _make_animation(
    file_id: str = "anim_123",
    duration: int = 5,
    width: int = 320,
    height: int = 240,
    file_size: int = 500_000,
) -> MagicMock:
    media = MagicMock()
    media.file_id = file_id
    media.duration = duration
    media.width = width
    media.height = height
    media.file_size = file_size
    media.file_name = None
    return media


def _make_message(**fields: MagicMock) -> MagicMock:
    """Create a mock message with specified media fields set."""
    msg = MagicMock()
    # Default all media fields to None
    msg.photo = None
    msg.voice = None
    msg.audio = None
    msg.document = None
    msg.sticker = None
    msg.video = None
    msg.video_note = None
    msg.animation = None
    msg.caption = None

    for field, value in fields.items():
        setattr(msg, field, value)

    return msg


# --- Test: generate_media_filename ---


class TestGenerateMediaFilename:
    def test_photo_uses_extension(self) -> None:
        """Photo gets UUID.jpg filename."""
        descriptor = MEDIA_DESCRIPTORS[3]  # photo
        media = _make_photo()
        result = generate_media_filename(descriptor, media)

        assert result.endswith(".jpg")
        assert len(result.split(".")[0]) == 12  # UUID hex part

    def test_voice_uses_extension(self) -> None:
        """Voice gets UUID.ogg filename."""
        descriptor = MEDIA_DESCRIPTORS[4]  # voice
        media = _make_voice()
        result = generate_media_filename(descriptor, media)

        assert result.endswith(".ogg")

    def test_document_preserves_original_filename(self) -> None:
        """Document preserves original filename appended to UUID."""
        descriptor = MEDIA_DESCRIPTORS[7]  # document
        media = _make_document(file_name="report.pdf")
        result = generate_media_filename(descriptor, media)

        assert result.endswith("-report.pdf")
        assert len(result.split("-")[0]) == 12

    def test_document_without_filename_uses_no_extension(self) -> None:
        """Document without original filename gets UUID only."""
        descriptor = MEDIA_DESCRIPTORS[7]  # document
        media = _make_document(file_name=None)
        result = generate_media_filename(descriptor, media)

        assert "." not in result
        assert len(result) == 12

    def test_audio_with_filename_preserves_extension(self) -> None:
        """Audio with original filename preserves it."""
        descriptor = MEDIA_DESCRIPTORS[6]  # audio
        media = _make_audio(file_name="song.flac")
        result = generate_media_filename(descriptor, media)

        assert result.endswith("-song.flac")

    def test_audio_without_filename_uses_mp3(self) -> None:
        """Audio without original filename gets .mp3."""
        descriptor = MEDIA_DESCRIPTORS[6]  # audio
        media = _make_audio(file_name=None)
        result = generate_media_filename(descriptor, media)

        assert result.endswith(".mp3")

    def test_sticker_regular(self) -> None:
        """Regular sticker gets .webp."""
        descriptor = MEDIA_DESCRIPTORS[1]  # sticker
        media = _make_sticker(is_animated=False, is_video=False)
        result = generate_media_filename(descriptor, media)

        assert result.endswith(".webp")

    def test_sticker_animated(self) -> None:
        """Animated sticker gets .tgs."""
        descriptor = MEDIA_DESCRIPTORS[1]
        media = _make_sticker(is_animated=True, is_video=False)
        result = generate_media_filename(descriptor, media)

        assert result.endswith(".tgs")

    def test_sticker_video(self) -> None:
        """Video sticker gets .webm."""
        descriptor = MEDIA_DESCRIPTORS[1]
        media = _make_sticker(is_animated=False, is_video=True)
        result = generate_media_filename(descriptor, media)

        assert result.endswith(".webm")


# --- Test: resolve_media ---


class TestResolveMedia:
    def test_photo_message(self) -> None:
        """Photo message resolves with correct descriptor."""
        photo = _make_photo()
        msg = _make_message(photo=[photo])
        result = resolve_media(msg)

        assert result is not None
        media_obj, descriptor = result
        assert descriptor.field_name == "photo"
        assert media_obj is photo  # photo[-1] of single-item list

    def test_animation_before_document(self) -> None:
        """Animation takes priority when both fields are set."""
        animation = _make_animation()
        document = _make_document()
        msg = _make_message()
        msg.animation = animation
        msg.document = document

        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "animation"

    def test_no_media_returns_none(self) -> None:
        """Message with no media fields returns None."""
        msg = _make_message()
        result = resolve_media(msg)

        assert result is None

    def test_voice_message(self) -> None:
        """Voice message resolves correctly."""
        voice = _make_voice()
        msg = _make_message(voice=voice)
        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "voice"

    def test_sticker_message(self) -> None:
        """Sticker message resolves correctly."""
        sticker = _make_sticker()
        msg = _make_message(sticker=sticker)
        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "sticker"

    def test_video_message(self) -> None:
        """Video message resolves correctly."""
        video = _make_video()
        msg = _make_message(video=video)
        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "video"

    def test_video_note_message(self) -> None:
        """Video note message resolves correctly."""
        vnote = _make_video_note()
        msg = _make_message(video_note=vnote)
        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "video_note"

    def test_audio_message(self) -> None:
        """Audio message resolves correctly."""
        audio = _make_audio()
        msg = _make_message(audio=audio)
        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "audio"

    def test_document_message(self) -> None:
        """Document message resolves correctly."""
        doc = _make_document()
        msg = _make_message(document=doc)
        result = resolve_media(msg)

        assert result is not None
        _, descriptor = result
        assert descriptor.field_name == "document"

    def test_descriptor_order_is_correct(self) -> None:
        """Verify descriptor table ordering matches design."""
        field_names = [d.field_name for d in MEDIA_DESCRIPTORS]
        assert field_names == [
            "animation", "sticker", "video_note", "photo",
            "voice", "video", "audio", "document",
        ]


# --- Test: metadata builders ---


class TestMetadataBuilders:
    def test_photo_metadata(self) -> None:
        """Photo metadata includes dimensions and file size."""
        descriptor = MEDIA_DESCRIPTORS[3]  # photo
        media = _make_photo(width=1280, height=720, file_size=250_000)
        lines = descriptor.build_metadata(media)

        assert "1280 × 720" in lines[0]
        assert "KB" in lines[1]

    def test_voice_metadata(self) -> None:
        """Voice metadata includes duration and MIME type."""
        descriptor = MEDIA_DESCRIPTORS[4]  # voice
        media = _make_voice(duration=12, mime_type="audio/ogg")
        lines = descriptor.build_metadata(media)

        assert "12 seconds" in lines[0]
        assert "audio/ogg" in lines[1]

    def test_voice_metadata_no_mime_type(self) -> None:
        """Voice metadata omits MIME type when None."""
        descriptor = MEDIA_DESCRIPTORS[4]  # voice
        media = _make_voice(duration=8, mime_type=None)
        lines = descriptor.build_metadata(media)

        assert len(lines) == 1
        assert "8 seconds" in lines[0]

    def test_audio_metadata_full(self) -> None:
        """Audio metadata includes title, performer, duration."""
        descriptor = MEDIA_DESCRIPTORS[6]  # audio
        media = _make_audio(title="Song", performer="Artist", duration=180)
        lines = descriptor.build_metadata(media)

        assert "Song" in lines[0]
        assert "Artist" in lines[1]
        assert "180 seconds" in lines[2]

    def test_audio_metadata_missing_fields(self) -> None:
        """Audio metadata omits missing title/performer."""
        descriptor = MEDIA_DESCRIPTORS[6]  # audio
        media = _make_audio(title=None, performer=None)
        lines = descriptor.build_metadata(media)

        assert len(lines) == 1
        assert "seconds" in lines[0]

    def test_document_metadata(self) -> None:
        """Document metadata includes filename, MIME type, size."""
        descriptor = MEDIA_DESCRIPTORS[7]  # document
        media = _make_document(
            file_name="report.pdf",
            mime_type="application/pdf",
            file_size=1_500_000,
        )
        lines = descriptor.build_metadata(media)

        assert "report.pdf" in lines[0]
        assert "application/pdf" in lines[1]
        assert "MB" in lines[2]

    def test_sticker_metadata_regular(self) -> None:
        """Regular sticker metadata includes emoji and format."""
        descriptor = MEDIA_DESCRIPTORS[1]  # sticker
        media = _make_sticker(emoji="😊", is_animated=False, is_video=False)
        lines = descriptor.build_metadata(media)

        assert "😊" in lines[0]
        assert "regular" in lines[1]

    def test_sticker_metadata_animated(self) -> None:
        """Animated sticker shows animated format."""
        descriptor = MEDIA_DESCRIPTORS[1]
        media = _make_sticker(is_animated=True, is_video=False)
        lines = descriptor.build_metadata(media)

        assert any("animated" in line for line in lines)

    def test_video_metadata(self) -> None:
        """Video metadata includes duration and dimensions."""
        descriptor = MEDIA_DESCRIPTORS[5]  # video
        media = _make_video(duration=60, width=1920, height=1080)
        lines = descriptor.build_metadata(media)

        assert "60 seconds" in lines[0]
        assert "1920 × 1080" in lines[1]

    def test_video_note_metadata(self) -> None:
        """Video note metadata includes duration and diameter."""
        descriptor = MEDIA_DESCRIPTORS[2]  # video_note
        media = _make_video_note(duration=30, length=240)
        lines = descriptor.build_metadata(media)

        assert "30 seconds" in lines[0]
        assert "Diameter: 240px" in lines[1]

    def test_animation_metadata(self) -> None:
        """Animation metadata includes duration and dimensions."""
        descriptor = MEDIA_DESCRIPTORS[0]  # animation
        media = _make_animation(duration=5, width=320, height=240)
        lines = descriptor.build_metadata(media)

        assert "5 seconds" in lines[0]
        assert "320 × 240" in lines[1]


# --- Test: download_media ---


class TestDownloadMedia:
    async def test_successful_download(self) -> None:
        """Successful download returns destination path."""
        bot = MagicMock()
        bot.download = AsyncMock(return_value=None)
        media = _make_photo()
        dest = Path("/tmp/test/photo.jpg")

        result = await download_media(bot, media, dest)

        assert result == dest
        bot.download.assert_called_once_with(media, destination=dest)

    async def test_file_too_large(self) -> None:
        """File exceeding limit raises MediaTooLargeError."""
        bot = MagicMock()
        media = _make_photo(file_size=25 * 1024 * 1024)  # 25 MB
        dest = Path("/tmp/test/photo.jpg")

        with pytest.raises(MediaTooLargeError) as exc_info:
            await download_media(bot, media, dest)

        assert exc_info.value.file_size == 25 * 1024 * 1024
        bot.download.assert_not_called()

    async def test_file_size_none_proceeds(self) -> None:
        """File with unknown size proceeds to download."""
        bot = MagicMock()
        bot.download = AsyncMock(return_value=None)
        media = _make_photo(file_size=None)
        dest = Path("/tmp/test/photo.jpg")

        result = await download_media(bot, media, dest)

        assert result == dest
        bot.download.assert_called_once()

    async def test_api_error_propagates(self) -> None:
        """TelegramAPIError propagates from download."""
        bot = MagicMock()
        bot.download = AsyncMock(side_effect=TelegramAPIError(method="download", message="fail"))
        media = _make_photo()
        dest = Path("/tmp/test/photo.jpg")

        with pytest.raises(TelegramAPIError):
            await download_media(bot, media, dest)


# --- Test: build_description ---


class TestBuildDescription:
    def test_with_caption(self) -> None:
        """Description includes caption when provided."""
        result = build_description(
            label="Photo",
            metadata_lines=["1280 × 720", "245 KB"],
            file_path=Path("/tmp/tachikoma-media/abc.jpg"),
            caption="Check this out",
        )

        assert "The user sent a Photo (1280 × 720, 245 KB)." in result
        assert "The file is saved at /tmp/tachikoma-media/abc.jpg" in result
        assert 'The user said: "Check this out"' in result

    def test_without_caption(self) -> None:
        """Description omits caption line when None."""
        result = build_description(
            label="Voice message",
            metadata_lines=["12 seconds", "audio/ogg"],
            file_path=Path("/tmp/tachikoma-media/abc.ogg"),
            caption=None,
        )

        assert "The user sent a Voice message (12 seconds, audio/ogg)." in result
        assert "The file is saved at" in result
        assert "The user said" not in result

    def test_empty_caption(self) -> None:
        """Description omits caption line when empty string."""
        result = build_description(
            label="Photo",
            metadata_lines=["1280 × 720"],
            file_path=Path("/tmp/tachikoma-media/abc.jpg"),
            caption="",
        )

        assert "The user said" not in result


# --- Test: _format_file_size ---


class TestFormatFileSize:
    def test_bytes(self) -> None:
        assert _format_file_size(500) == "500 B"

    def test_kilobytes(self) -> None:
        assert _format_file_size(250_000) == "244 KB"

    def test_megabytes(self) -> None:
        assert _format_file_size(2_000_000) == "1.9 MB"

    def test_none(self) -> None:
        assert _format_file_size(None) == "size unknown"


# --- Test: media_hook ---


class TestMediaHook:
    async def test_creates_directory_when_missing(
        self, tmp_path: Path, mocker: MockerFixture,
    ) -> None:
        """Hook creates media temp directory when it doesn't exist."""
        media_dir = tmp_path / "media"
        ctx = MagicMock()

        mocker.patch("tachikoma.media.MEDIA_TEMP_DIR", media_dir)
        await media_hook(ctx)

        assert media_dir.exists()
        assert media_dir.is_dir()

    async def test_deletes_old_files(
        self, tmp_path: Path, mocker: MockerFixture,
    ) -> None:
        """Hook deletes files older than MEDIA_CLEANUP_DAYS."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        old_file = media_dir / "old.jpg"
        old_file.write_text("old")
        old_time = time.time() - (45 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        ctx = MagicMock()

        mocker.patch("tachikoma.media.MEDIA_TEMP_DIR", media_dir)
        await media_hook(ctx)

        assert not old_file.exists()

    async def test_preserves_new_files(
        self, tmp_path: Path, mocker: MockerFixture,
    ) -> None:
        """Hook preserves files newer than MEDIA_CLEANUP_DAYS."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        new_file = media_dir / "new.jpg"
        new_file.write_text("new")

        ctx = MagicMock()

        mocker.patch("tachikoma.media.MEDIA_TEMP_DIR", media_dir)
        await media_hook(ctx)

        assert new_file.exists()

    async def test_idempotent_empty_dir(
        self, tmp_path: Path, mocker: MockerFixture,
    ) -> None:
        """Hook is idempotent on existing empty directory."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        ctx = MagicMock()

        mocker.patch("tachikoma.media.MEDIA_TEMP_DIR", media_dir)
        await media_hook(ctx)
        await media_hook(ctx)

        assert media_dir.exists()

    async def test_deletion_failure_logged_not_fatal(
        self, tmp_path: Path, mocker: MockerFixture,
    ) -> None:
        """Deletion failure is logged but doesn't abort hook."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        old_file = media_dir / "old.jpg"
        old_file.write_text("old")
        old_time = time.time() - (45 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        ctx = MagicMock()

        mocker.patch("tachikoma.media.MEDIA_TEMP_DIR", media_dir)
        mocker.patch.object(Path, "unlink", side_effect=OSError("permission denied"))
        await media_hook(ctx)

        # File still exists (unlink failed)
        assert old_file.exists()

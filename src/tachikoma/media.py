"""Media handling for Telegram channel.

Provides media descriptor table, download, description building,
file naming, and a bootstrap hook for temp folder management.
"""

import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from aiogram import Bot
from loguru import logger

from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="media")

# Constants
MEDIA_TEMP_DIR = Path("/tmp/tachikoma-media")
TELEGRAM_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MEDIA_CLEANUP_DAYS = 30


class MediaTooLargeError(Exception):
    """Raised when a media file exceeds the Telegram bot download limit."""

    def __init__(self, file_size: int) -> None:
        self.file_size = file_size
        super().__init__(
            f"File too large to download ({file_size / (1024 * 1024):.1f} MB). "
            f"Telegram bots can only download files up to 20 MB."
        )


class MediaDescriptor(NamedTuple):
    """Descriptor for a supported media type."""

    field_name: str
    accessor: Callable[[Any], Any]
    resolve_extension: Callable[[Any], str]
    label: str
    build_metadata: Callable[[Any], list[str]]


# --- Metadata builders ---


def _photo_metadata(media: Any) -> list[str]:
    return [
        f"{media.width} × {media.height}",
        _format_file_size(media.file_size),
    ]


def _voice_metadata(media: Any) -> list[str]:
    lines = [f"{media.duration} seconds"]
    if media.mime_type:
        lines.append(media.mime_type)
    return lines


def _audio_metadata(media: Any) -> list[str]:
    parts: list[str] = []
    if media.title:
        parts.append(media.title)
    if media.performer:
        parts.append(media.performer)
    parts.append(f"{media.duration} seconds")
    return parts


def _document_metadata(media: Any) -> list[str]:
    lines: list[str] = []
    if media.file_name:
        lines.append(media.file_name)
    if media.mime_type:
        lines.append(media.mime_type)
    if media.file_size is not None:
        lines.append(_format_file_size(media.file_size))
    return lines


def _sticker_metadata(media: Any) -> list[str]:
    lines: list[str] = []
    if media.emoji:
        lines.append(f"Emoji: {media.emoji}")
    if media.is_animated:
        lines.append("Format: animated (.tgs)")
    elif media.is_video:
        lines.append("Format: video (.webm)")
    else:
        lines.append("Format: regular (.webp)")
    return lines


def _video_metadata(media: Any) -> list[str]:
    return [
        f"{media.duration} seconds",
        f"{media.width} × {media.height}",
    ]


def _video_note_metadata(media: Any) -> list[str]:
    return [
        f"{media.duration} seconds",
        f"Diameter: {media.length}px",
    ]


def _animation_metadata(media: Any) -> list[str]:
    return [
        f"{media.duration} seconds",
        f"{media.width} × {media.height}",
    ]


# --- Extension resolvers ---


def _extension_from_filename(media: Any, fallback: str) -> str:
    """Extract file extension from media's file_name, or return fallback."""
    file_name = getattr(media, "file_name", None)
    if file_name:
        ext = Path(file_name).suffix
        if ext:
            return ext
    return fallback


def _sticker_extension(media: Any) -> str:
    if media.is_animated:
        return ".tgs"
    if media.is_video:
        return ".webm"
    return ".webp"


# --- Descriptor table ---
# Order matters: more specific types before generic ones.
# animation before document (animations set both fields),
# video_note before video (defensive),
# document last (most generic).

MEDIA_DESCRIPTORS: list[MediaDescriptor] = [
    MediaDescriptor(
        field_name="animation",
        accessor=lambda msg: msg.animation,
        resolve_extension=lambda _: ".mp4",
        label="Animation",
        build_metadata=_animation_metadata,
    ),
    MediaDescriptor(
        field_name="sticker",
        accessor=lambda msg: msg.sticker,
        resolve_extension=_sticker_extension,
        label="Sticker",
        build_metadata=_sticker_metadata,
    ),
    MediaDescriptor(
        field_name="video_note",
        accessor=lambda msg: msg.video_note,
        resolve_extension=lambda _: ".mp4",
        label="Video note",
        build_metadata=_video_note_metadata,
    ),
    MediaDescriptor(
        field_name="photo",
        accessor=lambda msg: msg.photo[-1] if msg.photo else None,
        resolve_extension=lambda _: ".jpg",
        label="Photo",
        build_metadata=_photo_metadata,
    ),
    MediaDescriptor(
        field_name="voice",
        accessor=lambda msg: msg.voice,
        resolve_extension=lambda _: ".ogg",
        label="Voice message",
        build_metadata=_voice_metadata,
    ),
    MediaDescriptor(
        field_name="video",
        accessor=lambda msg: msg.video,
        resolve_extension=lambda m: _extension_from_filename(m, ".mp4"),
        label="Video",
        build_metadata=_video_metadata,
    ),
    MediaDescriptor(
        field_name="audio",
        accessor=lambda msg: msg.audio,
        resolve_extension=lambda m: _extension_from_filename(m, ".mp3"),
        label="Audio file",
        build_metadata=_audio_metadata,
    ),
    MediaDescriptor(
        field_name="document",
        accessor=lambda msg: msg.document,
        resolve_extension=lambda m: _extension_from_filename(m, ""),
        label="Document",
        build_metadata=_document_metadata,
    ),
]


def resolve_media(message: Any) -> tuple[Any, MediaDescriptor] | None:
    """Resolve the first matching media descriptor for a message.

    Iterates MEDIA_DESCRIPTORS in priority order and returns the
    first (media_object, descriptor) where the accessor finds a
    non-None value. Returns None if no media matches.
    """
    for descriptor in MEDIA_DESCRIPTORS:
        media_obj = descriptor.accessor(message)
        if media_obj is not None:
            return (media_obj, descriptor)
    return None


def generate_media_filename(descriptor: MediaDescriptor, media_object: Any) -> str:
    """Generate a unique filename for a downloaded media file.

    Types with an original file_name (documents, audio, video) preserve
    it appended to a UUID. Others use UUID + type-appropriate extension.
    """
    unique_id = uuid.uuid4().hex[:12]
    original_name = getattr(media_object, "file_name", None)

    if original_name:
        return f"{unique_id}-{original_name}"

    ext = descriptor.resolve_extension(media_object)
    return f"{unique_id}{ext}"


async def download_media(bot: Bot, media_object: Any, dest_path: Path) -> Path:
    """Download a media file from Telegram.

    Pre-checks file size against TELEGRAM_MAX_FILE_SIZE when available.
    Raises MediaTooLargeError if the file exceeds the limit.
    Raises TelegramAPIError on download failure.
    """
    file_size = getattr(media_object, "file_size", None)
    if file_size is not None and file_size > TELEGRAM_MAX_FILE_SIZE:
        raise MediaTooLargeError(file_size)

    await bot.download(media_object, destination=dest_path)
    return dest_path


def build_description(
    label: str,
    metadata_lines: list[str],
    file_path: Path,
    caption: str | None,
) -> str:
    """Build a natural-language description for a downloaded media file.

    Format:
        "The user sent a {label} ({metadata}).
         The file is saved at {file_path}"
         [Optional: 'The user said: "{caption}"']
    """
    metadata_str = ", ".join(metadata_lines)
    parts = [f"The user sent a {label} ({metadata_str})."]
    parts.append(f"The file is saved at {file_path}")

    if caption:
        parts.append(f'The user said: "{caption}"')

    return "\n".join(parts)


def _format_file_size(size: int | None) -> str:
    """Format file size in human-readable form."""
    if size is None:
        return "size unknown"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# --- Bootstrap hook ---


async def media_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: ensure media temp directory exists and clean old files.

    Per DES-003, this hook is defined in the media module and registered
    in __main__.py. It is idempotent — safe to run every launch.
    """
    _log.debug("Ensuring media temp directory exists: path={path}", path=str(MEDIA_TEMP_DIR))

    MEDIA_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Clean files older than MEDIA_CLEANUP_DAYS
    now = time.time()
    cutoff = now - (MEDIA_CLEANUP_DAYS * 24 * 60 * 60)
    cleaned = 0
    errors = 0

    for item in MEDIA_TEMP_DIR.iterdir():
        if not item.is_file():
            continue

        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                cleaned += 1
        except OSError:
            _log.warning("Failed to delete old media file: path={path}", path=str(item))
            errors += 1

    _log.info(
        "Media temp directory ready: path={path}, cleaned={cleaned}, errors={errors}",
        path=str(MEDIA_TEMP_DIR),
        cleaned=cleaned,
        errors=errors,
    )

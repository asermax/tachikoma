"""send_file MCP tool server for delivering files via Telegram.

Follows DES-006: factory pattern with closure-captured state,
extracted handler for testability, Pydantic model for arg validation.
"""

from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field

# Extension-to-send-method mapping
MEDIA_TYPES: dict[str, set[str]] = {
    "photo": {".png", ".jpg", ".jpeg", ".gif", ".webp"},
    "audio": {".mp3", ".ogg", ".wav", ".flac"},
    "video": {".mp4", ".avi", ".mov", ".webm"},
}


def detect_media_type(path: Path) -> str:
    """Detect media type from file extension.

    Returns one of: "photo", "audio", "video", or "document" (fallback).
    """
    suffix = path.suffix.lower()

    for category, extensions in MEDIA_TYPES.items():
        if suffix in extensions:
            return category

    return "document"


def validate_file_path(file_path: str, workspace_path: Path) -> Path:
    """Validate and resolve a file path within the workspace.

    Args:
        file_path: Absolute or workspace-relative path.
        workspace_path: The workspace root directory.

    Returns:
        Resolved absolute Path.

    Raises:
        ValueError: If file doesn't exist or is outside workspace.
    """
    path = Path(file_path)

    if not path.is_absolute():
        path = workspace_path / path

    resolved = path.resolve()

    if not resolved.exists():
        raise ValueError(f"File not found: {resolved}")

    if not resolved.is_relative_to(workspace_path.resolve()):
        raise ValueError(f"File must be within the workspace: {resolved}")

    return resolved


class SendFileArgs(BaseModel):
    """Arguments for the send_file tool."""

    file_path: str
    caption: str | None = Field(default=None, max_length=1024)


async def handle_send_file(
    file_path: str,
    caption: str | None,
    bot: Bot,
    chat_id: int,
    workspace_path: Path,
) -> dict:
    """Handle a send_file tool call.

    Validates the file path, detects media type, and sends via
    the appropriate Telegram API method.
    """
    # Validate file path
    try:
        resolved = validate_file_path(file_path, workspace_path)
    except ValueError as e:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": str(e)}],
        }

    # Detect media type and send
    media_type = detect_media_type(resolved)
    file_input = FSInputFile(resolved)

    try:
        if media_type == "photo":
            await bot.send_photo(chat_id, photo=file_input, caption=caption)
        elif media_type == "audio":
            await bot.send_audio(chat_id, audio=file_input, caption=caption)
        elif media_type == "video":
            await bot.send_video(chat_id, video=file_input, caption=caption)
        else:
            await bot.send_document(chat_id, document=file_input, caption=caption)

        return {
            "content": [{"type": "text", "text": f"File sent: {resolved.name}"}],
        }

    except TelegramAPIError as e:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": str(e)}],
        }


def create_send_file_server(
    bot: Bot,
    chat_id: int,
    workspace_path: Path,
) -> McpSdkServerConfig:
    """Create MCP tool server for the send_file tool.

    Args:
        bot: The aiogram Bot instance for sending files.
        chat_id: The Telegram chat ID to send files to.
        workspace_path: The workspace root for file path validation.

    Returns:
        McpSdkServerConfig for registration with the coordinator.
    """

    @tool(
        "send_file",
        "Send a file to the user via Telegram.\n"
        "\n"
        "Parameters:\n"
        "- file_path (str, required): Path to the file — absolute or relative to the workspace\n"
        "- caption (str, optional, max 1024 chars): Brief description of the file\n"
        "\n"
        "Supported media types (auto-detected from extension):\n"
        "- Images (.png, .jpg, .jpeg, .gif, .webp) → sent as photo\n"
        "- Audio (.mp3, .ogg, .wav, .flac) → sent as audio\n"
        "- Video (.mp4, .avi, .mov, .webm) → sent as video\n"
        "- All other files → sent as document\n"
        "\n"
        "The file must exist within the workspace. Telegram enforces a 50MB upload limit.",
        SendFileArgs.model_json_schema(),
    )
    async def send_file(args: dict) -> dict:
        parsed = SendFileArgs.model_validate(args)
        return await handle_send_file(
            parsed.file_path,
            parsed.caption,
            bot,
            chat_id,
            workspace_path,
        )

    return create_sdk_mcp_server(
        name="send-file",
        tools=[send_file],
    )

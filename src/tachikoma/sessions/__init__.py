"""Sessions package: persistent conversation session tracking."""

from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.hooks import session_recovery_hook
from tachikoma.sessions.model import (
    ChannelMessage,
    MessageDirection,
    Session,
    SessionContextEntry,
    SessionResumption,
    SessionStatus,
)
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.sessions.repository import SessionRepository

__all__ = [
    "ChannelMessage",
    "MessageDirection",
    "Session",
    "SessionContextEntry",
    "SessionResumption",
    "SessionStatus",
    "SessionRegistry",
    "SessionRepository",
    "SessionRepositoryError",
    "session_recovery_hook",
]

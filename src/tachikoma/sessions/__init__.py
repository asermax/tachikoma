"""Sessions package: persistent conversation session tracking.

Public API for DLT-027: Track conversation sessions.
"""

from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.hooks import session_recovery_hook
from tachikoma.sessions.model import Session, SessionStatus
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.sessions.repository import SessionRepository

__all__ = [
    "Session",
    "SessionStatus",
    "SessionRegistry",
    "SessionRepository",
    "SessionRepositoryError",
    "session_recovery_hook",
]

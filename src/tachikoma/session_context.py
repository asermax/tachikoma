"""Shared mutable reference for the current SDK session ID.

Created once in ``__main__.py`` and passed to both the coordinator
(writes) and MCP tool servers (reads). See ADR-014.
"""


class SessionContext:
    """Mutable container for the current SDK session ID.

    The coordinator updates this after each message exchange.
    MCP tool servers read it when they need to fork the session.
    """

    def __init__(self) -> None:
        self._sdk_session_id: str | None = None

    def set(self, session_id: str | None) -> None:
        self._sdk_session_id = session_id

    def get(self) -> str | None:
        return self._sdk_session_id

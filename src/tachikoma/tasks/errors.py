"""Task domain exceptions.

Provides a clean error contract so callers never need to import SQLAlchemy.
"""


class TaskRepositoryError(Exception):
    """Raised when a task persistence operation fails.

    Wraps the underlying SQLAlchemy (or I/O) exception as __cause__
    so callers can inspect the root cause without importing SQLAlchemy.
    """

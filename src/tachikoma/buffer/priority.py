from enum import IntEnum


class Priority(IntEnum):
    """Priority level for buffered items.

    IntEnum so the value sorts directly as the primary heap key.
    Lower values are higher priority (delivered first).
    """

    URGENT = 1
    NORMAL = 2
    LOW = 3

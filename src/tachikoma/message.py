"""Message envelope types for the coordinator boundary."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    """Transport envelope at the coordinator enqueue boundary.

    Carries the message text and optional pinned skill names.  Regular user
    messages construct this with text only (``pinned_skills`` defaults empty).
    Session task deliveries carry skill names that were declared on the task
    definition.

    This is a thin interim envelope — a future typed message envelope will
    subsume it with a richer type carrying content-type and media metadata.
    """

    text: str
    pinned_skills: tuple[str, ...] = ()

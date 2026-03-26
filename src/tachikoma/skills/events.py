"""Typed event classes for the skills subsystem.

Events are dispatched on the bubus EventBus and consumed by subsystems
that need to react to skill changes (e.g., context invalidation).
"""

from bubus import BaseEvent


class SkillsChanged(BaseEvent[None]):
    """Event dispatched when skill files change on disk.

    Dispatched by the skills filesystem watcher after debounce settles.
    Consumers that need details about what changed should query the
    SkillRegistry directly.

    This event carries no payload — it signals "something changed" and
    lets consumers decide what action to take (e.g., re-classify skills,
    invalidate cached context).
    """

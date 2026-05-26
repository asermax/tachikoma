"""Message envelope types for the coordinator boundary."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class MessageEnvelope(ABC):
    """Abstract base for the coordinator's message envelopes.

    Every envelope can render itself into an SDK input string via
    ``sdk_input``.  Subtypes override property hooks for behavior
    that differs from the defaults (e.g. pre-processing opt-out).
    """

    @property
    @abstractmethod
    def sdk_input(self) -> str: ...

    @property
    def pinned_skills(self) -> tuple[str, ...]:
        return ()

    @property
    def force_new(self) -> bool:
        return False

    @property
    def runs_pre_processing(self) -> bool:
        return True

    @property
    def runs_boundary_detection(self) -> bool:
        return True

    @property
    def target_session_id(self) -> str | None:
        return None


@dataclass(frozen=True)
class TextMessage(MessageEnvelope):
    """Envelope for typed user input (and any non-tap producer)."""

    text: str
    pinned_skills: tuple[str, ...] = ()
    force_new: bool = False
    target_session_id: str | None = None
    external_id: str | None = None

    @property
    def sdk_input(self) -> str:
        return self.text


@dataclass(frozen=True)
class ButtonTapMessage(MessageEnvelope):
    """Envelope for a Telegram inline-button tap."""

    value: str
    target_session_id: str | None = None

    @property
    def sdk_input(self) -> str:
        return f"The user tapped the option `{self.value}` out of the options you displayed."

    @property
    def runs_pre_processing(self) -> bool:
        return False


@dataclass(frozen=True)
class ReactionMessage(MessageEnvelope):
    """Envelope for a Telegram emoji reaction change."""

    added: frozenset[str]
    removed: frozenset[str]
    target_session_id: str | None = None
    external_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "added", frozenset(self.added))
        object.__setattr__(self, "removed", frozenset(self.removed))
        if not self.added and not self.removed:
            msg = "ReactionMessage requires at least one added or removed emoji"
            raise ValueError(msg)
        if self.added & self.removed:
            msg = f"added and removed must be disjoint, overlap: {self.added & self.removed}"
            raise ValueError(msg)

    @property
    def sdk_input(self) -> str:
        suffix = "Interpret it in the context of the last exchange and respond accordingly."
        sorted_added = sorted(self.added)
        sorted_removed = sorted(self.removed)

        if self.removed and not self.added:
            emojis = self._join(sorted_removed)
            plural = "reactions" if len(sorted_removed) >= 2 else "reaction"
            pronoun = "these" if len(sorted_removed) >= 2 else "it"
            return (
                f"The user removed their {emojis} {plural}. "
                f"Interpret {pronoun} in the context of the last exchange "
                "and respond accordingly."
            )

        if self.added and not self.removed:
            emojis = self._join(sorted_added)
            pronoun = "these" if len(sorted_added) >= 2 else "it"
            return (
                f"The user reacted with {emojis}. "
                f"Interpret {pronoun} in the context of the last exchange "
                "and respond accordingly."
            )

        if len(self.added) == 1 and len(self.removed) == 1:
            old = sorted_removed[0]
            new = sorted_added[0]
            return f"The user changed their reaction from {old} to {new}. {suffix}"

        added_str = self._join(sorted_added)
        plural = "reactions" if len(sorted_removed) >= 2 else "reaction"
        removed_str = self._join(sorted_removed)
        return (
            f"The user reacted with {added_str} "
            f"and removed their {removed_str} {plural}. "
            "Interpret these in the context of the last exchange "
            "and respond accordingly."
        )

    @staticmethod
    def _join(items: list[str]) -> str:
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f" and {items[-1]}"

    @property
    def runs_pre_processing(self) -> bool:
        return False

    @property
    def runs_boundary_detection(self) -> bool:
        return False

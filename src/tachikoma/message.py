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


@dataclass(frozen=True)
class TextMessage(MessageEnvelope):
    """Envelope for typed user input (and any non-tap producer)."""

    text: str
    pinned_skills: tuple[str, ...] = ()
    force_new: bool = False

    @property
    def sdk_input(self) -> str:
        return self.text

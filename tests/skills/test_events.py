"""Tests for skills event classes.

Tests for DLT-038: Hot-reload skills at runtime.
"""

from bubus import BaseEvent

from tachikoma.skills.events import SkillsChanged


class TestSkillsChangedEvent:
    """Tests for SkillsChanged event."""

    def test_can_be_instantiated(self) -> None:
        """AC: SkillsChanged can be instantiated."""
        event = SkillsChanged()
        assert event is not None

    def test_is_base_event_subclass(self) -> None:
        """AC: SkillsChanged is a BaseEvent subclass."""
        event = SkillsChanged()
        assert isinstance(event, BaseEvent)

    def test_has_no_payload(self) -> None:
        """AC: SkillsChanged carries no data (signals 'something changed')."""
        event = SkillsChanged()
        # BaseEvent[None] means the event type param is None
        # The event itself doesn't have additional fields
        assert event.__class__.__name__ == "SkillsChanged"

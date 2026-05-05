"""Tests for event type registry: auto-discovery and name resolution.

Covers all AC from the "Event Type Registry" block of the spec.
"""

from __future__ import annotations

import pytest
from bubus import BaseEvent

from tachikoma.buffer.events import CoordinatorIdle
from tachikoma.plugins.registry import build_event_registry, get_event_type


class TestEventRegistryDiscovery:
    """Registry auto-discovers all BaseEvent subclasses."""

    def test_registry_contains_expected_event_types(self) -> None:
        """AC: All 8 event types are present in the registry."""
        registry = build_event_registry()
        expected = {
            "coordinator_idle",
            "notification",
            "skills_changed",
            "plugin_installed",
            "plugin_removing",
            "plugin_removed",
            "buffered_delivery",
            "restart_requested",
        }
        assert expected <= set(registry.keys())

    def test_each_mapping_resolves_to_base_event_subclass(self) -> None:
        """AC: Each snake_case name maps to a correct BaseEvent subclass."""
        registry = build_event_registry()
        for name, cls in registry.items():
            assert issubclass(cls, BaseEvent), f"{name} -> {cls} is not a BaseEvent subclass"


class TestGetEventType:
    """get_event_type() resolves names and rejects unknowns."""

    def test_valid_name_returns_correct_class(self) -> None:
        """AC: coordinator_idle resolves to CoordinatorIdle."""
        assert get_event_type("coordinator_idle") is CoordinatorIdle

    def test_unknown_name_raises_key_error(self) -> None:
        """AC: Unknown event type raises KeyError with valid names."""
        with pytest.raises(KeyError, match="unknown_event"):
            get_event_type("unknown_event")

    def test_key_error_lists_valid_types(self) -> None:
        """AC: KeyError message includes valid event type names."""
        with pytest.raises(KeyError, match="coordinator_idle") as exc_info:
            get_event_type("bogus")
        assert "notification" in str(exc_info.value)

    def test_idempotent_calls(self) -> None:
        """AC: Repeated calls return the same registry."""
        r1 = build_event_registry()
        r2 = build_event_registry()
        assert r1 is r2

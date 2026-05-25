"""Tests for the message envelope hierarchy."""

import re

import pytest

from tachikoma.message import (
    ButtonTapMessage,
    MessageEnvelope,
    ReactionMessage,
    TextMessage,
)


class TestTextMessage:
    def test_sdk_input_returns_text(self):
        assert TextMessage("hi").sdk_input == "hi"

    def test_default_pinned_skills_empty(self):
        assert TextMessage("hi").pinned_skills == ()

    def test_default_force_new_false(self):
        assert TextMessage("hi").force_new is False

    def test_default_runs_pre_processing_true(self):
        assert TextMessage("hi").runs_pre_processing is True

    def test_default_runs_boundary_detection_true(self):
        assert TextMessage("hi").runs_boundary_detection is True

    def test_custom_fields(self):
        msg = TextMessage("hi", pinned_skills=("a", "b"), force_new=True)
        assert msg.text == "hi"
        assert msg.pinned_skills == ("a", "b")
        assert msg.force_new is True

    def test_frozen(self):
        msg = TextMessage("hi")
        with pytest.raises(AttributeError):
            msg.text = "changed"  # type: ignore[misc]


class TestButtonTapMessage:
    def test_sdk_input_returns_explicit_tap_prose(self):
        msg = ButtonTapMessage("approve")
        assert msg.sdk_input == (
            "The user tapped the option `approve` out of the options you displayed."
        )

    def test_sdk_input_with_different_value(self):
        assert "`cancel`" in ButtonTapMessage("cancel").sdk_input

    def test_runs_pre_processing_false(self):
        assert ButtonTapMessage("yes").runs_pre_processing is False

    def test_default_runs_boundary_detection_true(self):
        assert ButtonTapMessage("yes").runs_boundary_detection is True

    def test_default_pinned_skills_empty(self):
        assert ButtonTapMessage("yes").pinned_skills == ()

    def test_default_force_new_false(self):
        assert ButtonTapMessage("yes").force_new is False

    def test_is_envelope(self):
        assert isinstance(ButtonTapMessage("yes"), MessageEnvelope)

    def test_frozen(self):
        msg = ButtonTapMessage("yes")
        with pytest.raises(AttributeError):
            msg.value = "no"  # type: ignore[misc]


class TestMessageEnvelopeABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            MessageEnvelope()  # type: ignore[abstract]

    def test_text_message_is_envelope(self):
        assert isinstance(TextMessage("hi"), MessageEnvelope)

    def test_button_tap_message_is_envelope(self):
        assert isinstance(ButtonTapMessage("approve"), MessageEnvelope)

    def test_reaction_message_is_envelope(self):
        assert isinstance(
            ReactionMessage(added=frozenset({"👍"}), removed=frozenset()),
            MessageEnvelope,
        )


# Prose content forbidden by R10 (no message target reference).
_FORBIDDEN_TARGET_REFS = re.compile(
    r"your message|the agent|my message|message #",
)
# Prose content forbidden by R4 (no emoji→meaning mapping).
_FORBIDDEN_SEMANTIC_REFS = re.compile(
    r"\b(approval|agree|disagree|confused)\b",
)


class TestReactionMessage:
    def test_is_envelope(self):
        assert isinstance(
            ReactionMessage(added=frozenset({"👍"}), removed=frozenset()),
            MessageEnvelope,
        )

    def test_frozen(self):
        msg = ReactionMessage(added=frozenset({"👍"}), removed=frozenset())
        with pytest.raises(AttributeError):
            msg.added = frozenset()  # type: ignore[misc]

    def test_inherited_defaults(self):
        msg = ReactionMessage(added=frozenset({"👍"}), removed=frozenset())
        assert msg.pinned_skills == ()
        assert msg.force_new is False

    def test_runs_pre_processing_false(self):
        msg = ReactionMessage(added=frozenset({"👍"}), removed=frozenset())
        assert msg.runs_pre_processing is False

    def test_runs_boundary_detection_false(self):
        msg = ReactionMessage(added=frozenset({"👍"}), removed=frozenset())
        assert msg.runs_boundary_detection is False

    def test_permissive_construction_both_empty(self):
        msg = ReactionMessage(added=frozenset(), removed=frozenset())
        assert msg.added == frozenset()
        assert msg.removed == frozenset()

    # --- Rendered prose: six diff shapes ---

    def test_added_single_emoji(self):
        msg = ReactionMessage(added=frozenset({"👍"}), removed=frozenset())
        assert msg.sdk_input == (
            "The user reacted with 👍. "
            "Interpret it in the context of the last exchange and respond accordingly."
        )

    def test_added_multiple_emojis(self):
        msg = ReactionMessage(added=frozenset({"👍", "❤"}), removed=frozenset())
        assert msg.sdk_input == (
            "The user reacted with ❤ and 👍. "
            "Interpret these in the context of the last exchange and respond accordingly."
        )

    def test_added_three_emojis_join_policy(self):
        msg = ReactionMessage(added=frozenset({"🤔", "👍", "❤"}), removed=frozenset())
        assert "❤, 👍 and 🤔" in msg.sdk_input

    def test_removed_single_emoji(self):
        msg = ReactionMessage(added=frozenset(), removed=frozenset({"👍"}))
        assert msg.sdk_input == (
            "The user removed their 👍 reaction. "
            "Interpret it in the context of the last exchange and respond accordingly."
        )

    def test_removed_multiple_emojis(self):
        msg = ReactionMessage(added=frozenset(), removed=frozenset({"👍", "❤"}))
        assert msg.sdk_input == (
            "The user removed their ❤ and 👍 reactions. "
            "Interpret these in the context of the last exchange and respond accordingly."
        )

    def test_replacement(self):
        msg = ReactionMessage(added=frozenset({"🤔"}), removed=frozenset({"👍"}))
        assert msg.sdk_input == (
            "The user changed their reaction from 👍 to 🤔. "
            "Interpret it in the context of the last exchange and respond accordingly."
        )

    def test_mixed_multiple_added_single_removed(self):
        msg = ReactionMessage(added=frozenset({"❤", "👍"}), removed=frozenset({"🤔"}))
        assert msg.sdk_input == (
            "The user reacted with ❤ and 👍 and removed their 🤔 reaction. "
            "Interpret these in the context of the last exchange and respond accordingly."
        )

    def test_mixed_single_added_multiple_removed(self):
        msg = ReactionMessage(added=frozenset({"👍"}), removed=frozenset({"🤔", "❤"}))
        assert msg.sdk_input == (
            "The user reacted with 👍 and removed their ❤ and 🤔 reactions. "
            "Interpret these in the context of the last exchange and respond accordingly."
        )

    # --- R10: prose never references a specific message target ---

    @pytest.mark.parametrize(
        "added,removed",
        [
            (frozenset({"👍"}), frozenset()),
            (frozenset(), frozenset({"👍"})),
            (frozenset({"🤔"}), frozenset({"👍"})),
            (frozenset({"❤", "👍"}), frozenset({"🤔"})),
        ],
    )
    def test_prose_never_references_message_target(self, added, removed):
        msg = ReactionMessage(added=added, removed=removed)
        assert _FORBIDDEN_TARGET_REFS.search(msg.sdk_input) is None
        assert not re.search(r"\d", msg.sdk_input)

    # --- R4: prose never maps emoji to meaning ---

    @pytest.mark.parametrize(
        "added,removed",
        [
            (frozenset({"👍"}), frozenset()),
            (frozenset(), frozenset({"👍"})),
            (frozenset({"🤔"}), frozenset({"👍"})),
            (frozenset({"❤", "👍"}), frozenset({"🤔"})),
        ],
    )
    def test_prose_never_maps_emoji_to_meaning(self, added, removed):
        msg = ReactionMessage(added=added, removed=removed)
        assert _FORBIDDEN_SEMANTIC_REFS.search(msg.sdk_input) is None

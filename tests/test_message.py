"""Tests for the message envelope hierarchy."""

import pytest

from tachikoma.message import ButtonTapMessage, MessageEnvelope, TextMessage


class TestTextMessage:
    def test_sdk_input_returns_text(self):
        assert TextMessage("hi").sdk_input == "hi"

    def test_default_pinned_skills_empty(self):
        assert TextMessage("hi").pinned_skills == ()

    def test_default_force_new_false(self):
        assert TextMessage("hi").force_new is False

    def test_default_runs_pre_processing_true(self):
        assert TextMessage("hi").runs_pre_processing is True

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
            "The user tapped the option `approve` "
            "out of the options you displayed."
        )

    def test_sdk_input_with_different_value(self):
        assert "`cancel`" in ButtonTapMessage("cancel").sdk_input

    def test_runs_pre_processing_false(self):
        assert ButtonTapMessage("yes").runs_pre_processing is False

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

"""Tests for sdk_query module.

Capture SDK stderr on error for debugging.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk import ProcessError
from claude_agent_sdk.types import ClaudeAgentOptions
from pytest_mock import MockerFixture

from tachikoma.sdk_query import StderrAccumulator, stderr_aware_query


class TestStderrAccumulator:
    """Tests for StderrAccumulator class."""

    def test_accumulates_lines(self) -> None:
        """AC: Multiple __call__ invocations, get() returns newline-joined."""
        acc = StderrAccumulator()
        acc("line 1")
        acc("line 2")
        acc("line 3")

        assert acc.get() == "line 1\nline 2\nline 3"

    def test_returns_none_when_empty(self) -> None:
        """AC: Fresh instance, get() returns None (no noise in logs)."""
        acc = StderrAccumulator()
        assert acc.get() is None

    def test_truncates_with_marker(self) -> None:
        """AC: Exceeding max_chars gets tail-truncated with marker prefix."""
        acc = StderrAccumulator()
        long_line = "x" * 5000
        acc(long_line)
        acc(long_line)
        acc(long_line)  # 15,000 chars total (3 lines of 5000 + 2 newlines)

        result = acc.get(max_chars=200)
        assert result is not None
        assert result.startswith("[stderr truncated]\n")
        # Tail content should be present (last ~200 chars of joined)
        assert len(result) < 5000  # Much shorter than original

    def test_swallows_callback_errors(self, mocker: MockerFixture) -> None:
        """AC: R3 — callback errors silently swallowed, no propagation."""
        acc = StderrAccumulator()

        # Replace _lines with a list-like object that raises on append
        class FailList(list):  # noqa: SLOT000
            def append(self, item: object) -> None:
                raise RuntimeError("memory error")

        acc._lines = FailList()
        acc("test")  # Should not raise

    def test_custom_max_chars(self) -> None:
        """AC: Custom threshold passed to get() is respected."""
        acc = StderrAccumulator()
        acc("a" * 100)

        result = acc.get(max_chars=50)
        assert result is not None
        assert result.startswith("[stderr truncated]\n")
        # Should keep only tail
        assert len(result) < 200

    def test_within_threshold_returned_in_full(self) -> None:
        """AC: R4 — output within threshold returned without truncation."""
        acc = StderrAccumulator()
        acc("short line")
        assert acc.get() == "short line"


class TestStderrAwareQuery:
    """Tests for stderr_aware_query() async generator."""

    async def test_yields_all_messages_on_success(self, mocker: MockerFixture) -> None:
        """AC: All messages from SDK query re-yielded, no log call."""
        msg1 = {"type": "assistant"}
        msg2 = {"type": "result"}

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            yield msg1
            yield msg2

        mock_query = mocker.patch(
            "tachikoma.sdk_query.query",
            side_effect=fake_query,
        )

        results = [msg async for msg in stderr_aware_query(prompt="test")]

        assert results == [msg1, msg2]
        mock_query.assert_called_once()

    async def test_logs_stderr_on_process_error(self, mocker: MockerFixture) -> None:
        """AC: R0 — ProcessError with stderr lines, log includes stderr= field."""

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            # Simulate SDK calling stderr callback before raising
            stderr_cb = kwargs["options"].stderr
            stderr_cb("error line 1")
            stderr_cb("error line 2")
            raise ProcessError("boom")
            yield  # type: ignore[unreachable]

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)
        mock_log = mocker.patch("tachikoma.sdk_query._log")

        with pytest.raises(ProcessError):
            [msg async for msg in stderr_aware_query(prompt="test")]

        # Verify error was logged with stderr field
        mock_log.error.assert_called_once()
        call_kwargs = mock_log.error.call_args
        assert "stderr" in call_kwargs[1]

    async def test_reraises_process_error_unchanged(self, mocker: MockerFixture) -> None:
        """AC: R3 — same exception object re-raised, propagation unchanged."""
        original = ProcessError("original error")

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            raise original
            yield  # type: ignore[unreachable]

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)
        mocker.patch("tachikoma.sdk_query._log")

        with pytest.raises(ProcessError) as exc_info:
            [msg async for msg in stderr_aware_query(prompt="test")]

        assert exc_info.value is original

    async def test_no_stderr_field_when_buffer_empty(self, mocker: MockerFixture) -> None:
        """AC: R0 AC2 — ProcessError with no stderr, log omits stderr kwarg."""

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            raise ProcessError("boom")
            yield  # type: ignore[unreachable]

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)
        mock_log = mocker.patch("tachikoma.sdk_query._log")

        with pytest.raises(ProcessError):
            [msg async for msg in stderr_aware_query(prompt="test")]

        mock_log.error.assert_called_once()
        call_args = mock_log.error.call_args
        # Should not have stderr in kwargs
        assert "stderr" not in call_args[1]

    async def test_logs_stderr_on_rewrapped_exception(self, mocker: MockerFixture) -> None:
        """AC1: Plain Exception (SDK re-wrapping) with stderr lines, log includes stderr= field."""

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            stderr_cb = kwargs["options"].stderr
            stderr_cb("real stderr line 1")
            stderr_cb("real stderr line 2")
            raise Exception("Check stderr output for details")
            yield  # type: ignore[unreachable]

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)
        mock_log = mocker.patch("tachikoma.sdk_query._log")

        with pytest.raises(Exception, match="Check stderr output for details"):
            [msg async for msg in stderr_aware_query(prompt="test")]

        mock_log.error.assert_called_once()
        call_kwargs = mock_log.error.call_args
        assert "stderr" in call_kwargs[1]
        assert "real stderr line 1" in call_kwargs[1]["stderr"]

    async def test_reraises_plain_exception_unchanged(self, mocker: MockerFixture) -> None:
        """AC4: Plain Exception re-raised with same identity (not wrapped further)."""
        original = Exception("SDK re-wrapped error")

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            raise original
            yield  # type: ignore[unreachable]

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)
        mocker.patch("tachikoma.sdk_query._log")

        with pytest.raises(Exception) as exc_info:
            [msg async for msg in stderr_aware_query(prompt="test")]

        assert exc_info.value is original

    async def test_no_stderr_on_plain_exception_without_stderr(self, mocker: MockerFixture) -> None:
        """AC2: Plain Exception with no stderr, log omits stderr kwarg."""

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            raise Exception("some error")
            yield  # type: ignore[unreachable]

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)
        mock_log = mocker.patch("tachikoma.sdk_query._log")

        with pytest.raises(Exception):
            [msg async for msg in stderr_aware_query(prompt="test")]

        mock_log.error.assert_called_once()
        call_args = mock_log.error.call_args
        assert "stderr" not in call_args[1]

    async def test_installs_accumulator_on_options(self, mocker: MockerFixture) -> None:
        """AC: options.stderr is set before SDK delegation."""
        captured_options: list[ClaudeAgentOptions] = []

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            captured_options.append(kwargs["options"])
            yield {"type": "result"}

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)

        opts = ClaudeAgentOptions()
        [msg async for msg in stderr_aware_query(prompt="test", options=opts)]

        assert captured_options[0].stderr is not None

    async def test_creates_options_when_none(self, mocker: MockerFixture) -> None:
        """AC: options=None creates fresh ClaudeAgentOptions with accumulator."""
        captured_options: list[Any] = []

        async def fake_query(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            captured_options.append(kwargs["options"])
            yield {"type": "result"}

        mocker.patch("tachikoma.sdk_query.query", side_effect=fake_query)

        [msg async for msg in stderr_aware_query(prompt="test", options=None)]

        assert captured_options[0].stderr is not None

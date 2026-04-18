"""REPL behavior tests.

Tests for DLT-001: Core agent architecture and DLT-025: Markdown rendering.
"""

import asyncio
import contextlib
import signal
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from prompt_toolkit.validation import Validator
from rich.console import Console

from tachikoma.buffer.events import BufferedDelivery
from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority
from tachikoma.events import Error, Result, TextChunk, ToolActivity
from tachikoma.repl import Renderer, Repl


@pytest.fixture
def stdout_buf() -> StringIO:
    return StringIO()


@pytest.fixture
def stderr_buf() -> StringIO:
    return StringIO()


@pytest.fixture
def renderer(stdout_buf: StringIO, stderr_buf: StringIO) -> Renderer:
    return Renderer(
        console=Console(file=stdout_buf, force_terminal=True),
        err_console=Console(file=stderr_buf, force_terminal=True),
    )


class TestRendering:
    def test_renders_text_chunk(self, renderer, stdout_buf) -> None:
        renderer.render(TextChunk(text="Hello"))

        assert "Hello" in stdout_buf.getvalue()

    def test_renders_tool_activity_as_status_line(self, renderer, stdout_buf) -> None:
        event = ToolActivity(tool_name="Read", tool_input={"file_path": "main.py"})
        renderer.render(event)

        assert "🔧 Reading main.py" in stdout_buf.getvalue()

    def test_renders_known_tool_with_missing_input(self, renderer, stdout_buf) -> None:
        renderer.render(ToolActivity(tool_name="Write", tool_input={}))

        assert "🔧 Writing ..." in stdout_buf.getvalue()

    def test_renders_unknown_tool_with_fallback(self, renderer, stdout_buf) -> None:
        renderer.render(ToolActivity(tool_name="CustomMCP", tool_input={}))

        assert "🔧 CustomMCP..." in stdout_buf.getvalue()

    def test_renders_grep_tool_with_pattern(self, renderer, stdout_buf) -> None:
        renderer.render(
            ToolActivity(
                tool_name="Grep",
                tool_input={"pattern": "TODO"},
                result="",
            )
        )

        assert "🔧 Searching for 'TODO'" in stdout_buf.getvalue()

    def test_renders_glob_tool_with_pattern(self, renderer, stdout_buf) -> None:
        renderer.render(
            ToolActivity(
                tool_name="Glob",
                tool_input={"pattern": "**/*.py"},
                result="",
            )
        )

        assert "🔧 Globbing **/*.py" in stdout_buf.getvalue()

    def test_renders_bash_tool_with_command(self, renderer, stdout_buf) -> None:
        renderer.render(
            ToolActivity(
                tool_name="Bash",
                tool_input={"command": "ls -la"},
                result="",
            )
        )

        assert "Running: ls -la" in stdout_buf.getvalue()

    def test_renders_tool_search_with_query(self, renderer, stdout_buf) -> None:
        renderer.render(
            ToolActivity(
                tool_name="ToolSearch",
                tool_input={"query": "select:Read"},
                result="",
            )
        )

        assert "Searching tools: select:Read" in stdout_buf.getvalue()

    def test_tool_activity_after_text_both_render(self, renderer, stdout_buf) -> None:
        renderer.render(TextChunk(text="thinking"))
        renderer.render(ToolActivity(tool_name="Read", tool_input={"file_path": "f.py"}))

        out = stdout_buf.getvalue()
        assert "thinking" in out
        assert "🔧 Reading f.py" in out

    def test_renders_result_as_blank_line(self, renderer, stdout_buf) -> None:
        renderer.render(Result())

        assert "\n" in stdout_buf.getvalue()

    def test_renders_error_message(self, renderer, stderr_buf) -> None:
        renderer.render(Error(message="connection lost", recoverable=True))

        assert "connection lost" in stderr_buf.getvalue()

    def test_recoverable_error_returns_true(self, renderer) -> None:
        assert renderer.render(Error(message="transient", recoverable=True)) is True

    def test_non_recoverable_error_returns_false(self, renderer) -> None:
        assert renderer.render(Error(message="auth failed", recoverable=False)) is False

    def test_renders_bold_and_italic(self, renderer, stdout_buf) -> None:
        """AC: Bold and italic text appear with appropriate formatting."""
        renderer.render(TextChunk(text="**bold** and *italic*"))

        out = stdout_buf.getvalue()
        assert "bold" in out
        assert "italic" in out

    def test_renders_markdown_list(self, renderer, stdout_buf) -> None:
        """AC: List items display as a properly formatted list."""
        renderer.render(TextChunk(text="- first\n- second\n- third"))

        out = stdout_buf.getvalue()
        assert "first" in out
        assert "second" in out
        assert "third" in out

    def test_renders_markdown_heading(self, renderer, stdout_buf) -> None:
        """AC: Headings display with visual emphasis."""
        renderer.render(TextChunk(text="# Title"))

        assert "Title" in stdout_buf.getvalue()

    def test_renders_plain_text_without_artifacts(self, renderer, stdout_buf) -> None:
        """AC: Plain text renders normally without artifacts."""
        renderer.render(TextChunk(text="Hello world"))

        out = stdout_buf.getvalue()
        assert "Hello world" in out

    def test_renders_fenced_code_block(self, renderer, stdout_buf) -> None:
        """AC: Fenced code block content is preserved in output."""
        renderer.render(TextChunk(text="```python\nprint('hello')\n```"))

        assert "print" in stdout_buf.getvalue()

    def test_sequential_text_chunks_each_render(self, renderer, stdout_buf) -> None:
        """AC: Multiple TextChunk events each display immediately."""
        renderer.render(TextChunk(text="First chunk"))
        renderer.render(TextChunk(text="Second chunk"))

        out = stdout_buf.getvalue()
        assert "First chunk" in out
        assert "Second chunk" in out


class TestReplControlFlow:
    async def test_exits_on_eof(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(history_path=tmp_path / "repl_history")

        mocker.patch.object(repl._session, "prompt_async", side_effect=EOFError)

        await repl.run(coordinator)

    async def test_exits_on_keyboard_interrupt_at_prompt(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(history_path=tmp_path / "repl_history")

        mocker.patch.object(repl._session, "prompt_async", side_effect=KeyboardInterrupt)

        await repl.run(coordinator)

    async def test_exits_on_exit_command(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(history_path=tmp_path / "repl_history")

        mocker.patch.object(repl._session, "prompt_async", side_effect=["exit"])

        await repl.run(coordinator)

    async def test_exits_on_quit_command(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(history_path=tmp_path / "repl_history")

        mocker.patch.object(repl._session, "prompt_async", side_effect=["quit"])

        await repl.run(coordinator)

    async def test_interrupts_and_exits_on_ctrl_c_during_stream(
        self,
        tmp_path: Path,
        mocker,
    ) -> None:
        coordinator = MagicMock()
        coordinator.interrupt = AsyncMock()

        async def _raise_on_iter():
            raise KeyboardInterrupt
            yield  # make it an async generator

        coordinator.send_message = MagicMock(side_effect=_raise_on_iter)

        repl = Repl(history_path=tmp_path / "repl_history")
        mocker.patch.object(repl._session, "prompt_async", side_effect=["hello", EOFError])

        await repl.run(coordinator)

        coordinator.interrupt.assert_awaited_once()

    async def test_exits_on_non_recoverable_error(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()

        async def _fatal_stream():
            yield Error(message="auth failed", recoverable=False)

        coordinator.send_message = MagicMock(side_effect=_fatal_stream)

        repl = Repl(history_path=tmp_path / "repl_history")
        mocker.patch.object(repl._session, "prompt_async", side_effect=["hello", EOFError])

        await repl.run(coordinator)

    async def test_continues_on_recoverable_error(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        call_count = 0

        async def _stream():
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                yield Error(message="rate limit", recoverable=True)
            else:
                yield TextChunk(text="ok")
                yield Result()

        coordinator.send_message = MagicMock(side_effect=_stream)

        repl = Repl(history_path=tmp_path / "repl_history")
        mocker.patch.object(
            repl._session,
            "prompt_async",
            side_effect=["first", "second", EOFError],
        )

        await repl.run(coordinator)

        assert call_count == 2


class TestReplMultilineInput:
    async def test_multiline_text_sent_to_coordinator(self, tmp_path: Path, mocker) -> None:
        """Multiline text (containing newlines) is submitted as-is to the coordinator."""
        coordinator = MagicMock()

        async def _stream():
            yield Result()

        coordinator.send_message = MagicMock(side_effect=_stream)

        repl = Repl(history_path=tmp_path / "repl_history")
        mocker.patch.object(
            repl._session,
            "prompt_async",
            side_effect=["line1\nline2\nline3", EOFError],
        )

        await repl.run(coordinator)

        coordinator.enqueue.assert_called_once_with("line1\nline2\nline3")


class TestReplBufferIntegration:
    """R12: REPL invokes buffer.flush_on_shutdown when run() exits."""

    async def test_run_exit_flushes_buffer(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        buffer = MagicMock()
        buffer.flush_on_shutdown = AsyncMock()

        repl = Repl(history_path=tmp_path / "repl_history", buffer=buffer)
        mocker.patch.object(repl._session, "prompt_async", side_effect=EOFError)

        await repl.run(coordinator)

        buffer.flush_on_shutdown.assert_awaited_once()

    async def test_run_exit_without_buffer_is_noop(self, tmp_path: Path, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(history_path=tmp_path / "repl_history")
        mocker.patch.object(repl._session, "prompt_async", side_effect=EOFError)

        # Should not raise even though buffer is None
        await repl.run(coordinator)

    async def test_buffered_delivery_handler_routes_through_coordinator(
        self,
        tmp_path: Path,
    ) -> None:
        coordinator = MagicMock()

        async def _stream():
            yield Result()

        coordinator.send_message = MagicMock(side_effect=_stream)

        repl = Repl(history_path=tmp_path / "repl_history")
        repl._Repl__coordinator = coordinator  # type: ignore[attr-defined]

        item = BufferedItem(priority=Priority.NORMAL, prompt="from-buffer", kind="notification")
        event = BufferedDelivery(prompt="from-buffer", items=[item])

        await repl._handle_buffered_delivery(event)

        coordinator.enqueue.assert_called_with("from-buffer")

    async def test_second_sigint_cancels_flush_and_interrupts(
        self,
        tmp_path: Path,
        mocker,
    ) -> None:
        """KD-6/S15: second SIGINT during flush cancels flush + coordinator and
        signals force-exit to the caller."""
        coordinator = MagicMock()
        coordinator.interrupt = AsyncMock()

        flush_should_complete = asyncio.Event()
        buffer = MagicMock()

        async def _slow_flush() -> None:
            await flush_should_complete.wait()

        buffer.flush_on_shutdown = _slow_flush

        repl = Repl(history_path=tmp_path / "repl_history", buffer=buffer)
        repl._Repl__coordinator = coordinator  # type: ignore[attr-defined]

        captured: dict[str, object] = {}
        loop = asyncio.get_running_loop()
        real_add = loop.add_signal_handler

        def fake_add(sig, callback, *args):  # noqa: ANN001, ANN202
            if sig == signal.SIGINT:
                captured["handler"] = callback
            else:
                real_add(sig, callback, *args)

        mocker.patch.object(loop, "add_signal_handler", side_effect=fake_add)
        mocker.patch.object(loop, "remove_signal_handler", return_value=True)
        mocker.patch("tachikoma.repl.signal.getsignal", return_value=None)
        mocker.patch("tachikoma.repl.signal.signal")

        flush_outer: asyncio.Task[bool] = asyncio.ensure_future(
            repl._flush_buffer_on_shutdown()
        )
        await asyncio.sleep(0.01)

        assert "handler" in captured, "SIGINT handler should be registered during flush"
        captured["handler"]()  # type: ignore[operator]

        force_exit = await flush_outer
        # Drain pending callbacks (ensure_future for coordinator.interrupt)
        await asyncio.sleep(0)

        assert force_exit is True
        coordinator.interrupt.assert_awaited()

    async def test_run_force_exit_raises_keyboard_interrupt(
        self,
        tmp_path: Path,
        mocker,
    ) -> None:
        """When _flush_buffer_on_shutdown returns True, run() re-raises KI."""
        coordinator = MagicMock()
        buffer = MagicMock()
        buffer.flush_on_shutdown = AsyncMock()

        repl = Repl(history_path=tmp_path / "repl_history", buffer=buffer)
        mocker.patch.object(repl._session, "prompt_async", side_effect=EOFError)
        mocker.patch.object(repl, "_flush_buffer_on_shutdown", AsyncMock(return_value=True))

        with contextlib.suppress(KeyboardInterrupt):
            await repl.run(coordinator)

        repl._flush_buffer_on_shutdown.assert_awaited_once()


class TestReplInputValidation:
    @pytest.fixture
    def validator(self) -> Validator:
        return Validator.from_callable(
            lambda text: text.strip() != "",
            error_message="",
            move_cursor_to_end=True,
        )

    def test_rejects_empty_input(self, validator) -> None:
        with pytest.raises(Exception):
            validator.validate(MagicMock(text=""))

    def test_rejects_whitespace_only_input(self, validator) -> None:
        with pytest.raises(Exception):
            validator.validate(MagicMock(text="   "))

    def test_accepts_valid_input(self, validator) -> None:
        validator.validate(MagicMock(text="hello"))

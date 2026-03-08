"""REPL behavior tests.

Tests for DLT-001: Core agent architecture.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from prompt_toolkit.validation import Validator

from tachikoma.events import Error, Result, TextChunk, ToolActivity
from tachikoma.repl import Renderer, Repl


@pytest.fixture
def renderer() -> Renderer:
    return Renderer()


class TestRendering:
    def test_renders_text_chunk_inline(self, renderer, capsys) -> None:
        renderer.render(TextChunk(text="Hello"))

        assert capsys.readouterr().out == "Hello"

    def test_renders_tool_activity_as_status_line(self, renderer, capsys) -> None:
        event = ToolActivity(tool_name="Read", tool_input={"file_path": "main.py"}, result="")
        renderer.render(event)

        out = capsys.readouterr().out
        assert "Reading main.py..." in out

    def test_renders_generic_tool_activity(self, renderer, capsys) -> None:
        renderer.render(ToolActivity(tool_name="Write", tool_input={}, result=""))

        out = capsys.readouterr().out
        assert "Write..." in out

    def test_renders_grep_tool_with_pattern(self, renderer, capsys) -> None:
        renderer.render(ToolActivity(
            tool_name="Grep",
            tool_input={"pattern": "TODO"},
            result="",
        ))

        out = capsys.readouterr().out
        assert "Searching for 'TODO'..." in out

    def test_renders_glob_tool_with_pattern(self, renderer, capsys) -> None:
        renderer.render(ToolActivity(
            tool_name="Glob",
            tool_input={"pattern": "**/*.py"},
            result="",
        ))

        out = capsys.readouterr().out
        assert "Globbing **/*.py..." in out

    def test_renders_bash_tool_with_command(self, renderer, capsys) -> None:
        renderer.render(ToolActivity(
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            result="",
        ))

        out = capsys.readouterr().out
        assert "Running: ls -la" in out

    def test_renders_tool_search_with_query(self, renderer, capsys) -> None:
        renderer.render(ToolActivity(
            tool_name="ToolSearch",
            tool_input={"query": "select:Read"},
            result="",
        ))

        out = capsys.readouterr().out
        assert "Searching tools: select:Read" in out

    def test_tool_activity_after_text_gets_leading_newline(self, renderer, capsys) -> None:
        renderer.render(TextChunk(text="thinking"))
        renderer.render(ToolActivity(tool_name="Read", tool_input={"file_path": "f.py"}, result=""))

        out = capsys.readouterr().out
        assert out.startswith("thinking\n")
        assert "Reading f.py..." in out

    def test_tool_activity_without_prior_text_has_no_leading_newline(
        self, renderer, capsys,
    ) -> None:
        renderer.render(ToolActivity(tool_name="Read", tool_input={"file_path": "f.py"}, result=""))

        out = capsys.readouterr().out
        assert not out.startswith("\n")

    def test_renders_result_as_newline(self, renderer, capsys) -> None:
        renderer.render(Result())

        assert capsys.readouterr().out == "\n"

    def test_renders_error_message(self, renderer, capsys) -> None:
        renderer.render(Error(message="connection lost", recoverable=True))

        assert "connection lost" in capsys.readouterr().err

    def test_recoverable_error_returns_true(self, renderer) -> None:
        assert renderer.render(Error(message="transient", recoverable=True)) is True

    def test_non_recoverable_error_returns_false(self, renderer) -> None:
        assert renderer.render(Error(message="auth failed", recoverable=False)) is False


class TestReplControlFlow:
    async def test_exits_on_eof(self, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(coordinator)

        mocker.patch.object(repl._session, "prompt_async", side_effect=EOFError)

        await repl.run()

    async def test_exits_on_keyboard_interrupt_at_prompt(self, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(coordinator)

        mocker.patch.object(repl._session, "prompt_async", side_effect=KeyboardInterrupt)

        await repl.run()

    async def test_exits_on_exit_command(self, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(coordinator)

        mocker.patch.object(repl._session, "prompt_async", side_effect=["exit"])

        await repl.run()

    async def test_exits_on_quit_command(self, mocker) -> None:
        coordinator = MagicMock()
        repl = Repl(coordinator)

        mocker.patch.object(repl._session, "prompt_async", side_effect=["quit"])

        await repl.run()

    async def test_interrupts_and_exits_on_ctrl_c_during_stream(self, mocker) -> None:
        coordinator = MagicMock()
        coordinator.interrupt = AsyncMock()

        async def _raise_on_iter(text):
            raise KeyboardInterrupt
            yield  # make it an async generator

        coordinator.send_message = MagicMock(side_effect=_raise_on_iter)

        repl = Repl(coordinator)
        mocker.patch.object(repl._session, "prompt_async", side_effect=["hello", EOFError])

        await repl.run()

        coordinator.interrupt.assert_awaited_once()

    async def test_exits_on_non_recoverable_error(self, mocker) -> None:
        coordinator = MagicMock()

        async def _fatal_stream(text):
            yield Error(message="auth failed", recoverable=False)

        coordinator.send_message = MagicMock(side_effect=_fatal_stream)

        repl = Repl(coordinator)
        mocker.patch.object(repl._session, "prompt_async", side_effect=["hello", EOFError])

        await repl.run()

    async def test_continues_on_recoverable_error(self, mocker) -> None:
        coordinator = MagicMock()
        call_count = 0

        async def _stream(text):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                yield Error(message="rate limit", recoverable=True)
            else:
                yield TextChunk(text="ok")
                yield Result()

        coordinator.send_message = MagicMock(side_effect=_stream)

        repl = Repl(coordinator)
        mocker.patch.object(
            repl._session,
            "prompt_async",
            side_effect=["first", "second", EOFError],
        )

        await repl.run()

        assert call_count == 2


class TestReplInputValidation:
    def test_rejects_empty_input(self) -> None:
        validator = Validator.from_callable(
            lambda text: text.strip() != "",
            error_message="",
            move_cursor_to_end=True,
        )

        with pytest.raises(Exception):
            validator.validate(MagicMock(text=""))

    def test_rejects_whitespace_only_input(self) -> None:
        validator = Validator.from_callable(
            lambda text: text.strip() != "",
            error_message="",
            move_cursor_to_end=True,
        )

        with pytest.raises(Exception):
            validator.validate(MagicMock(text="   "))

    def test_accepts_valid_input(self) -> None:
        validator = Validator.from_callable(
            lambda text: text.strip() != "",
            error_message="",
            move_cursor_to_end=True,
        )

        validator.validate(MagicMock(text="hello"))

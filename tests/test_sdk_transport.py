"""Tests for FilePromptTransport.

Verifies that the custom transport writes the system prompt to a tempfile
and uses --append-system-prompt-file instead of --append-system-prompt.
"""

from claude_agent_sdk.types import ClaudeAgentOptions, SystemPromptPreset

from tachikoma.sdk_transport import FilePromptTransport


def _make_transport(append: str) -> FilePromptTransport:
    """Create a FilePromptTransport with a system prompt append string."""
    options = ClaudeAgentOptions(
        system_prompt=SystemPromptPreset(
            type="preset",
            preset="claude_code",
            append=append,
        ),
        permission_mode="bypassPermissions",
    )

    async def _empty_stream():
        return
        yield {}  # type: ignore[unreachable]

    return FilePromptTransport(prompt=_empty_stream(), options=options)


class TestFilePromptTransport:
    """Tests for FilePromptTransport._build_command() and cleanup."""

    def test_uses_file_flag_instead_of_inline(self) -> None:
        """AC: Command uses --append-system-prompt-file, not --append-system-prompt."""
        transport = _make_transport("Hello world")
        cmd = transport._build_command()

        assert "--append-system-prompt-file" in cmd
        assert "--append-system-prompt" not in [
            c for c in cmd if c != "--append-system-prompt-file"
        ]

    def test_tempfile_contains_prompt_content(self) -> None:
        """AC: Tempfile is created with the system prompt content."""
        transport = _make_transport("Test prompt content")
        transport._build_command()

        assert transport._prompt_file is not None
        assert transport._prompt_file.exists()
        assert transport._prompt_file.read_text() == "Test prompt content"

    async def test_tempfile_cleaned_up_on_close(self) -> None:
        """AC: Tempfile is removed when transport is closed."""
        transport = _make_transport("Cleanup test")
        transport._build_command()

        prompt_file = transport._prompt_file
        assert prompt_file is not None
        assert prompt_file.exists()

        # Null out SDK internal attributes so the parent close() doesn't
        # try to terminate a subprocess that was never started.  These are
        # SubprocessCLITransport internals — update if the SDK changes.
        transport._process = None
        transport._stdout_stream = None
        transport._stdin_stream = None
        transport._stderr_stream = None
        transport._stderr_task_group = None

        await transport.close()

        assert not prompt_file.exists()
        assert transport._prompt_file is None

    def test_no_file_when_no_append(self) -> None:
        """AC: No tempfile created when system prompt has no append."""
        options = ClaudeAgentOptions(
            system_prompt=SystemPromptPreset(
                type="preset",
                preset="claude_code",
            ),
            permission_mode="bypassPermissions",
        )

        async def _empty_stream():
            return
            yield {}  # type: ignore[unreachable]

        transport = FilePromptTransport(prompt=_empty_stream(), options=options)
        cmd = transport._build_command()

        assert "--append-system-prompt-file" not in cmd
        assert transport._prompt_file is None

    def test_large_content_handled(self) -> None:
        """AC: Large system prompts are written to file without issue."""
        large_content = "x" * 500_000  # 500KB
        transport = _make_transport(large_content)
        cmd = transport._build_command()

        assert "--append-system-prompt-file" in cmd
        assert transport._prompt_file is not None
        assert len(transport._prompt_file.read_text()) == 500_000

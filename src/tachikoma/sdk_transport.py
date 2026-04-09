"""Custom SDK transport that passes the system prompt via a tempfile.

The Claude Agent SDK's ``SubprocessCLITransport`` passes the
``--append-system-prompt`` value as a CLI argument, which counts toward
the OS ``ARG_MAX`` limit (~2 MB on Linux).  This module provides a
subclass that writes the append string to a temporary file and uses
``--append-system-prompt-file`` instead, eliminating that constraint.
"""

import tempfile
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

# SubprocessCLITransport is internal to the SDK — not part of the public API.
# Pinned to known-working SDK v0.1.48; verify on upgrades.
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions


class FilePromptTransport(SubprocessCLITransport):
    """Transport that delivers the system prompt append via a tempfile.

    Overrides ``_build_command()`` to swap
    ``--append-system-prompt <content>`` for
    ``--append-system-prompt-file <path>``.  The tempfile is cleaned up
    when the transport is closed.
    """

    def __init__(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeAgentOptions,
    ) -> None:
        super().__init__(prompt, options)
        self._prompt_file: Path | None = None

    def _build_command(self) -> list[str]:
        cmd = super()._build_command()

        try:
            idx = cmd.index("--append-system-prompt")
        except ValueError:
            return cmd

        # The content sits in the next element
        content = cmd[idx + 1]

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            prefix="tachikoma-prompt-",
            delete=False,
        ) as tf:
            tf.write(content)
            self._prompt_file = Path(tf.name)

        cmd[idx] = "--append-system-prompt-file"
        cmd[idx + 1] = str(self._prompt_file)

        return cmd

    async def close(self) -> None:
        await super().close()

        if self._prompt_file is not None:
            self._prompt_file.unlink(missing_ok=True)
            self._prompt_file = None

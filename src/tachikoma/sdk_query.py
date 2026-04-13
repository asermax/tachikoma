"""Stderr capture wrapper for Claude Agent SDK calls.

Provides a ``StderrAccumulator`` that receives stderr lines from the SDK
subprocess via the ``ClaudeAgentOptions.stderr`` callback, and a
``stderr_aware_query()`` async generator that wraps the SDK's ``query()``
with automatic stderr accumulation and error logging.

See DLT-098 design for rationale.
"""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from claude_agent_sdk import ProcessError, query
from claude_agent_sdk.types import ClaudeAgentOptions
from loguru import logger

_log = logger.bind(component="sdk_query")

_TRUNCATION_MARKER = "[stderr truncated]\n"


class StderrAccumulator:
    """Stateful callable that buffers stderr lines from the SDK subprocess.

    Installed as ``ClaudeAgentOptions.stderr`` so the SDK transport pipes
    stderr output per-line into the buffer.  On error, ``get()`` returns
    the accumulated output (tail-truncated if over *max_chars*).  On
    success the instance is simply discarded.

    All internal errors (e.g. list.append failing under memory pressure)
    are silently swallowed so that stderr capture never causes secondary
    failures (R3).
    """

    def __init__(self) -> None:
        self._lines: list[str] = []

    def __call__(self, line: str) -> None:
        try:  # noqa: SIM105
            self._lines.append(line)
        except Exception:
            pass  # R3: silently swallow - observer, not participant

    def get(self, max_chars: int = 10_000) -> str | None:
        """Return joined stderr lines, or ``None`` when buffer is empty.

        If the total length exceeds *max_chars*, the output is truncated
        to the tail (most recent lines) with a ``[stderr truncated]`` prefix
        (R4).
        """
        if not self._lines:
            return None

        joined = "\n".join(self._lines)

        if len(joined) <= max_chars:
            return joined

        # Tail-truncation: keep the most recent content (R4)
        truncated = joined[-max_chars:]
        return _TRUNCATION_MARKER + truncated


async def stderr_aware_query(
    *,
    prompt: str | AsyncIterable[dict[str, Any]],
    options: ClaudeAgentOptions | None = None,
    transport: Any | None = None,
) -> AsyncIterator[Any]:
    """Drop-in replacement for SDK ``query()`` that logs stderr on error.

    Creates a ``StderrAccumulator``, installs it on ``options.stderr``, and
    delegates to the SDK's ``query()``.  On ``ProcessError``, the accumulated
    stderr is logged as a structured field per DES-002 and the exception is
    re-raised unchanged.  On success, the accumulator is discarded.

    Follows DES-005: all messages from the inner generator are re-yielded
    so the generator is fully consumed by the caller.
    """
    if options is None:
        options = ClaudeAgentOptions()

    accumulator = StderrAccumulator()
    options.stderr = accumulator

    try:
        async for message in query(
            prompt=prompt,
            options=options,
            transport=transport,
        ):
            yield message
    except ProcessError as exc:
        stderr = accumulator.get()
        if stderr is not None:
            _log.error(
                "SDK query failed: err={err}, stderr={stderr}",
                err=str(exc),
                stderr=stderr,
            )
        else:
            _log.error("SDK query failed: err={err}", err=str(exc))
        raise

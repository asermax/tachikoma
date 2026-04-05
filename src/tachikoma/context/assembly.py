"""Context assembly utilities for building system prompts from persisted entries.

The assembly function builds the system prompt append string from:
1. Rendered system preamble (timezone-aware template)
2. Persisted SessionContextEntry instances (wrapped in XML tags by owner)
"""

from tachikoma.context.loading import render_system_preamble
from tachikoma.sessions.model import SessionContextEntry


def build_system_prompt(
    entries: list[SessionContextEntry], *, timezone: str
) -> str:
    """Build system prompt append string from persisted context entries.

    This is a pure function that:
    - Always prepends the rendered system preamble
    - Wraps each entry's content in <owner> XML tags
    - Returns entries in insertion order (determined by entry.id)
    - Returns the rendered preamble alone when entries list is empty (graceful degradation fallback)

    Args:
        entries: List of SessionContextEntry instances to assemble.
            Order is determined by caller (typically by entry.id ascending).
        timezone: Valid IANA timezone string (pre-validated by config).

    Returns:
        The complete system prompt append string.
    """
    preamble = render_system_preamble(timezone)

    if not entries:
        return preamble

    sections = [f"<{e.owner}>\n{e.content}\n</{e.owner}>" for e in entries]

    return preamble + "\n\n" + "\n\n".join(sections)

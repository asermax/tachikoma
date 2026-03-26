"""Context assembly utilities for building system prompts from persisted entries.

DLT-041: Persist session context to database.

The assembly function builds the system prompt append string from:
1. SYSTEM_PREAMBLE (hardcoded, always prepended)
2. Persisted SessionContextEntry instances (wrapped in XML tags by owner)
"""

from tachikoma.context.loading import SYSTEM_PREAMBLE
from tachikoma.sessions.model import SessionContextEntry


def build_system_prompt(entries: list[SessionContextEntry]) -> str:
    """Build system prompt append string from persisted context entries.

    This is a pure function that:
    - Always prepends SYSTEM_PREAMBLE
    - Wraps each entry's content in <owner> XML tags
    - Returns entries in insertion order (determined by entry.id)
    - Returns SYSTEM_PREAMBLE alone when entries list is empty (graceful degradation fallback)

    Args:
        entries: List of SessionContextEntry instances to assemble.
            Order is determined by caller (typically by entry.id ascending).

    Returns:
        The complete system prompt append string.

    See: DLT-041 design (S3) - pure function, XML wrapping, SYSTEM_PREAMBLE prepending.
    """
    if not entries:
        return SYSTEM_PREAMBLE

    sections = [f"<{e.owner}>\n{e.content}\n</{e.owner}>" for e in entries]

    return SYSTEM_PREAMBLE + "\n\n" + "\n\n".join(sections)

"""Context package: loading and updating foundational context files.

Provides startup context loading (context_hook), assembly (build_system_prompt),
and post-processing context updates (CoreContextProcessor).
"""

from tachikoma.context.assembly import build_system_prompt
from tachikoma.context.loading import (
    CONTEXT_DIR_NAME,
    CONTEXT_FILES,
    DEFAULT_AGENTS_CONTENT,
    DEFAULT_SOUL_CONTENT,
    DEFAULT_USER_CONTENT,
    SYSTEM_PREAMBLE_TEMPLATE,
    context_hook,
    load_foundational_context,
    render_system_preamble,
)
from tachikoma.context.processor import CoreContextProcessor

__all__ = [
    "CONTEXT_DIR_NAME",
    "CONTEXT_FILES",
    "CoreContextProcessor",
    "DEFAULT_AGENTS_CONTENT",
    "DEFAULT_SOUL_CONTENT",
    "DEFAULT_USER_CONTENT",
    "SYSTEM_PREAMBLE_TEMPLATE",
    "build_system_prompt",
    "context_hook",
    "load_foundational_context",
    "render_system_preamble",
]

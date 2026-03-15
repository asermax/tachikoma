"""Context package: loading and updating foundational context files.

Provides startup context loading (context_hook) and post-processing context
updates (CoreContextProcessor).
"""

from tachikoma.context.loading import (
    CONTEXT_DIR_NAME,
    CONTEXT_FILES,
    DEFAULT_AGENTS_CONTENT,
    DEFAULT_SOUL_CONTENT,
    DEFAULT_USER_CONTENT,
    SYSTEM_PREAMBLE,
    context_hook,
    load_context,
)
from tachikoma.context.processor import CoreContextProcessor

__all__ = [
    "CONTEXT_DIR_NAME",
    "CONTEXT_FILES",
    "CoreContextProcessor",
    "DEFAULT_AGENTS_CONTENT",
    "DEFAULT_SOUL_CONTENT",
    "DEFAULT_USER_CONTENT",
    "SYSTEM_PREAMBLE",
    "context_hook",
    "load_context",
]

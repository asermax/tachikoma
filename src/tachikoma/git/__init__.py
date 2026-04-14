"""Git module for workspace version tracking.

Provides git-based version control for workspace changes via:
- A bootstrap hook that initializes the workspace as a git repo
- A post-processor that commits and pushes workspace changes after each session
- Shared sync utilities for divergence detection and smart push/pull
"""

from tachikoma.git.hooks import git_hook
from tachikoma.git.processor import GitProcessor
from tachikoma.git.sync import (
    DIVERGENCE_STATUS,
    PUSH_RESULT,
    SYNC_RESULT,
    detect_divergence,
    smart_pull,
    smart_push,
)

__all__ = [
    "git_hook",
    "GitProcessor",
    "DIVERGENCE_STATUS",
    "PUSH_RESULT",
    "SYNC_RESULT",
    "detect_divergence",
    "smart_pull",
    "smart_push",
]

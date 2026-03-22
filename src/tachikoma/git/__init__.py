"""Git module for workspace version tracking.

Provides git-based version control for workspace changes via:
- A bootstrap hook that initializes the workspace as a git repo
- A post-processor that commits and pushes workspace changes after each session
"""

from tachikoma.git.hooks import git_hook
from tachikoma.git.processor import GitProcessor

__all__ = ["git_hook", "GitProcessor"]

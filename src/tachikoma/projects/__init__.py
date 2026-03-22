"""Project management for external git repositories.

Provides tools for registering, tracking, and committing changes in
external code repositories managed as git submodules.
"""

from tachikoma.projects.context_provider import ProjectsContextProvider
from tachikoma.projects.hooks import projects_hook
from tachikoma.projects.processor import ProjectsProcessor

__all__ = ["ProjectsContextProvider", "ProjectsProcessor", "projects_hook"]

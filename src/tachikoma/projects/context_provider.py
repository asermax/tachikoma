"""Context provider for injecting project awareness and MCP tools.

Provides a list of registered projects with their names, paths, and current branches
(or commit hash if detached HEAD). Also provides MCP tools for registering and
deregistering projects during the session.
"""

import asyncio
from pathlib import Path

from loguru import logger

from tachikoma.pre_processing import ContextProvider, ContextResult
from tachikoma.projects.git import (
    current_branch,
    current_commit_short,
    list_submodules,
)
from tachikoma.projects.tools import create_projects_server

_log = logger.bind(component="projects")


class ProjectsContextProvider(ContextProvider):
    """Context provider that injects project awareness and MCP tools.

    Discovers all registered submodules under projects/ and returns a ContextResult
    with project information and MCP tools. The tools are always available,
    even when no projects are registered (so the register_project can be used).
    """

    def __init__(self, workspace_path: Path) -> None:
        self._workspace_path = workspace_path
        self._server = create_projects_server(workspace_path)

    async def provide(self, message: str) -> ContextResult | None:
        """Provide context about registered projects.

        Always returns a ContextResult with MCP tools available, even when
        no projects exist. This ensures register_project can be used to add
        the first project.
        """
        try:
            submodule_paths = await list_submodules(self._workspace_path)
        except Exception as e:
            _log.warning("Failed to list submodules: err={err}", err=str(e))
            submodule_paths = []

        if not submodule_paths:
            return ContextResult(
                tag="projects",
                content="No projects registered. Use register_project to add one.",
                mcp_servers={"projects": self._server},
            )

        # Query all submodules in parallel
        infos = await asyncio.gather(
            *[self._get_project_info(path) for path in submodule_paths],
        )

        projects = [info for info in infos if info is not None]
        content = "## Registered Projects\n\n" + "\n".join(projects)

        return ContextResult(
            tag="projects",
            content=content,
            mcp_servers={"projects": self._server},
        )

    async def _get_project_info(self, path: str) -> str | None:
        """Get display info for a single submodule.

        Returns:
            Formatted string like "- my-app: main", or None on error.
        """
        name = path.rsplit("/", maxsplit=1)[-1] if "/" in path else path
        submodule_path = self._workspace_path / path

        try:
            branch = await current_branch(submodule_path)

            if branch is None:
                commit = await current_commit_short(submodule_path)
                return f"- {name}: {commit} (detached)"

            return f"- {name}: {branch}"
        except Exception as e:
            _log.warning(
                "Failed to get project info: path={path} err={err}",
                path=path,
                err=str(e),
            )
            return None

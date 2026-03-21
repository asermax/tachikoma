"""Context provider for injecting project awareness and MCP tools.


Provides a list of registered projects with their names, paths, and current branches
(or commit hash if detached HEAD). Also provides MCP tools for registering and
deregistering projects during the session.
"""

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

    Attributes:
        _workspace_path: The workspace root directory.
    """

    def __init__(self, workspace_path: Path) -> None:
        """Initialize the provider.

        Args:
            workspace_path: The workspace root directory.
        """
        self._workspace_path = workspace_path

    async def provide(self, message: str) -> ContextResult | None:
        """Provide context about registered projects.

        Always returns a ContextResult with MCP tools available, even when
        no projects exist. This ensures register_project can be used to add
        the first project.

        Args:
            message: The user's message (unused - projects context is static).

        Returns:
            ContextResult with project list and MCP tools, or None on error.
        """
        # Always create the MCP server (tools must be available even with no projects)
        server = create_projects_server(self._workspace_path)

        # List submodules
        try:
            submodule_paths = await list_submodules(self._workspace_path)
        except Exception as e:
            _log.warning("Failed to list submodules: err={err}", err=str(e))
            submodule_paths = []

        if not submodule_paths:
            # No projects — return tools only (minimal content)
            return ContextResult(
                tag="projects",
                content="No projects registered. Use register_project to add one.",
                mcp_servers={"projects": server},
            )

        # Build project info for each submodule
        projects: list[str] = []
        for path in submodule_paths:
            # Extract name from path (e.g., "projects/my-app" -> "my-app")
            name = path.split("/")[-1] if "/" in path else path
            submodule_path = self._workspace_path / path

            try:
                # Get current branch or commit hash
                branch = await current_branch(submodule_path)
                if branch is None:
                    # Detached HEAD - use commit hash
                    commit = await current_commit_short(submodule_path)
                    projects.append(f"- {name}: {commit} (detached)")
                else:
                    projects.append(f"- {name}: {branch}")
            except Exception as e:
                _log.warning(
                    "Failed to get project info: path={path} err={err}",
                    path=path,
                    err=str(e),
                )
                # Exclude corrupted/missing projects from context
                continue

        content = "## Registered Projects\n\n" + "\n".join(projects)

        return ContextResult(
            tag="projects",
            content=content,
            mcp_servers={"projects": server},
        )

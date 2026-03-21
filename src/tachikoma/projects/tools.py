"""MCP tools for project management.

Provides tools for registering and deregistering project submodules
during conversations.
"""

import contextlib
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig
from loguru import logger

from tachikoma.projects.git import (
    add_submodule,
    checkout_branch,
    has_uncommitted_changes_detail,
    remove_submodule,
    resolve_default_branch,
)

_log = logger.bind(component="projects")


def create_projects_server(workspace_path: Path) -> McpSdkServerConfig:
    """Create SDK MCP server with project management tools.

    The tools have closure over the workspace_path for git operations.

    Args:
        workspace_path: The workspace root directory.

    Returns:
        McpSdkServerConfig for use with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "register_project",
        "Register a new project as a git submodule under projects/<name>.",
        {"name": str, "url": str},
    )
    async def register_project(args: dict) -> dict:
        """Register a new project by adding it as a git submodule.

        Args:
            args: Must contain 'name' (project name) and 'url' (git remote URL).

        Returns:
            Success message or error with is_error=True.
        """
        name = args.get("name", "")
        url = args.get("url", "")

        if not name:
            return {
                "content": [{"type": "text", "text": "Error: 'name' is required"}],
                "is_error": True,
            }

        if not url:
            return {
                "content": [{"type": "text", "text": "Error: 'url' is required"}],
                "is_error": True,
            }

        project_path = workspace_path / "projects" / name

        if project_path.exists():
            return {
                "content": [
                    {"type": "text", "text": f"Error: Project '{name}' already exists"}
                ],
                "is_error": True,
            }

        try:
            await add_submodule(workspace_path, name, url)
            default_branch = await resolve_default_branch(project_path)
            await checkout_branch(project_path, default_branch)

            _log.info(
                "Project registered: name={name} url={url} branch={branch}",
                name=name,
                url=url,
                branch=default_branch,
            )

            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Registered project '{name}' (branch: {default_branch}). "
                        f"The project is now available under projects/{name}. "
                        f"Changes will be committed to the workspace at session end.",
                    }
                ]
            }

        except Exception as e:
            # Cleanup partial state on failure
            if project_path.exists():
                with contextlib.suppress(Exception):
                    await remove_submodule(workspace_path, name)

            error_msg = str(e)
            _log.warning(
                "Project registration failed: name={name} err={err}",
                name=name,
                err=error_msg,
            )

            return {
                "content": [
                    {"type": "text", "text": f"Error registering project: {error_msg}"}
                ],
                "is_error": True,
            }

    @tool(
        "deregister_project",
        "Remove a registered project. Warns if uncommitted changes exist unless force=true.",
        {"name": str, "force": bool},
    )
    async def deregister_project(args: dict) -> dict:
        """Deregister a project by removing the git submodule.

        Args:
            args: Must contain 'name'. Optional 'force' (default false).

        Returns:
            Success message, warning, or error with is_error=True.
        """
        name = args.get("name", "")
        force = args.get("force", False)

        if not name:
            return {
                "content": [{"type": "text", "text": "Error: 'name' is required"}],
                "is_error": True,
            }

        project_path = workspace_path / "projects" / name

        if not project_path.exists():
            return {
                "content": [
                    {"type": "text", "text": f"Error: Project '{name}' not found"}
                ],
                "is_error": True,
            }

        try:
            changes = await has_uncommitted_changes_detail(project_path)

            if changes and not force:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Warning: Project '{name}' has uncommitted changes:\n"
                            f"{changes}\n\n"
                            f"Use force=true to remove anyway (changes will be lost).",
                        }
                    ],
                    "is_error": True,
                }

            await remove_submodule(workspace_path, name)

            _log.info("Project deregistered: name={name} force={force}", name=name, force=force)

            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Deregistered project '{name}'. "
                        f"Changes will be committed to the workspace at session end.",
                    }
                ]
            }

        except Exception as e:
            error_msg = str(e)
            _log.warning(
                "Project deregistration failed: name={name} err={err}",
                name=name,
                err=error_msg,
            )

            return {
                "content": [
                    {"type": "text", "text": f"Error deregistering project: {error_msg}"}
                ],
                "is_error": True,
            }

    return create_sdk_mcp_server(
        name="projects",
        version="1.0.0",
        tools=[register_project, deregister_project],
    )

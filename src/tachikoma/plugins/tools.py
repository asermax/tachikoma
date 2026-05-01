"""MCP tools for plugin management (install, list, remove).

Follows DES-006 (SDK MCP tool server factory): handler logic extracted into
standalone async functions for direct testability, the factory wraps them with
``@tool()`` decorators that close over the ``PluginManager``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel, ValidationError, model_validator

from tachikoma.plugins.manager import (
    PluginAliasCollisionError,
    PluginInstallError,
    PluginNotFoundError,
)
from tachikoma.plugins.sources import parse_plugin_source

if TYPE_CHECKING:
    from tachikoma.plugins.manager import PluginManager

_log = logger.bind(component="plugins.tools")


# ---------------------------------------------------------------------------
# Pydantic arg models
# ---------------------------------------------------------------------------


class InstallPluginArgs(BaseModel):
    """Arguments for installing a plugin.

    Exactly one of ``git``, ``url``, or ``path`` must be provided.
    """

    git: str | None = None
    url: str | None = None
    path: str | None = None
    subdir: str | None = None
    ref: str | None = None
    alias: str | None = None

    @model_validator(mode="after")
    def _validate_source_exclusivity(self) -> InstallPluginArgs:
        provided = sum(f is not None for f in (self.git, self.url, self.path))
        if provided == 0:
            msg = "Provide exactly one of 'git', 'url', or 'path'."
            raise ValueError(msg)
        if provided > 1:
            msg = "Provide exactly one of 'git', 'url', or 'path', not multiple."
            raise ValueError(msg)
        return self


class RemovePluginArgs(BaseModel):
    alias: str


# ---------------------------------------------------------------------------
# Extracted handler functions (testable without SDK)
# ---------------------------------------------------------------------------


async def handle_install_plugin(args: InstallPluginArgs, manager: PluginManager) -> dict:
    """Install a plugin from a source declaration.

    Returns the MCP envelope dict (``is_error`` + ``content``).
    """
    raw = {}
    if args.git is not None:
        raw["git"] = args.git
    elif args.url is not None:
        raw["url"] = args.url
    elif args.path is not None:
        raw["path"] = args.path
    if args.subdir is not None:
        raw["subdir"] = args.subdir
    if args.ref is not None:
        raw["ref"] = args.ref

    try:
        source = parse_plugin_source(raw)
    except ValueError as exc:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": f"Invalid source: {exc}"}],
        }

    try:
        plugin = await manager.install(source, alias=args.alias)
    except PluginAliasCollisionError as exc:
        hint = ""
        if exc.suggest_retry_with_alias:
            hint = " Retry with an explicit 'alias' parameter to use a different name."
        return {
            "is_error": True,
            "content": [
                {"type": "text", "text": f"Plugin alias '{exc.alias}' already exists.{hint}"}
            ],
        }
    except PluginInstallError as exc:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": f"Install failed: {exc}"}],
        }

    skill_names = [s.qualified_name for s in plugin.contributed_skills]
    manifest_info = {}
    if plugin.manifest:
        manifest_info = {
            "name": plugin.manifest.name,
            "version": plugin.manifest.version,
            "description": plugin.manifest.description,
        }

    payload = {
        "alias": plugin.alias,
        "status": plugin.status,
        "manifest": manifest_info,
        "contributed_skills": skill_names,
    }

    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
    }


async def handle_list_plugins(manager: PluginManager) -> dict:
    """List all installed plugins.

    Returns the MCP envelope dict.
    """
    plugins = manager.list_plugins()

    if not plugins:
        return {
            "content": [{"type": "text", "text": "No plugins installed."}],
        }

    entries = []
    for p in plugins:
        entry: dict = {
            "alias": p.alias,
            "status": p.status,
        }
        if p.manifest:
            entry["name"] = p.manifest.name
            entry["version"] = p.manifest.version
            entry["description"] = p.manifest.description
        if p.status != "loaded" and p.diagnostic:
            entry["diagnostic"] = p.diagnostic
        if p.contributed_skills:
            entry["namespaced_skills"] = [s.qualified_name for s in p.contributed_skills]
        entries.append(entry)

    return {
        "content": [{"type": "text", "text": json.dumps(entries, indent=2)}],
    }


async def handle_remove_plugin(args: RemovePluginArgs, manager: PluginManager) -> dict:
    """Remove a plugin by alias.

    Returns the MCP envelope dict.
    """
    try:
        diagnostic = await manager.remove(args.alias)
    except PluginNotFoundError as exc:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": str(exc)}],
        }

    result = {"alias": args.alias, "removed": True}
    if diagnostic:
        result["diagnostic"] = diagnostic

    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_plugin_tools_server(manager: PluginManager) -> McpSdkServerConfig:
    """Create an MCP server exposing plugin management tools.

    Args:
        manager: The ``PluginManager`` instance to operate on.

    Returns:
        ``McpSdkServerConfig`` for registration with ``ClaudeAgentOptions.mcp_servers``.
    """

    @tool(
        "install_plugin",
        "Install a plugin from a git repository, URL archive, or local path.\n"
        "\n"
        "Parameters:\n"
        "- git (str, optional): Git repository URL to clone from.\n"
        "- url (str, optional): URL to download a plugin archive from.\n"
        "- path (str, optional): Local filesystem path to copy from.\n"
        "- subdir (str, optional): Subdirectory within the source to use.\n"
        "- ref (str, optional): Git ref (branch/tag) to checkout.\n"
        "- alias (str, optional): Explicit alias; defaults to manifest name.\n"
        "\n"
        "Provide exactly one of 'git', 'url', or 'path'.",
        InstallPluginArgs.model_json_schema(),
    )
    async def install_plugin(args: dict) -> dict:
        try:
            parsed = InstallPluginArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }
        return await handle_install_plugin(parsed, manager)

    @tool(
        "list_plugins",
        "List all installed plugins.\n"
        "\n"
        "Returns each plugin's alias, status, manifest info, and namespaced skills.",
        {},
    )
    async def list_plugins(args: dict) -> dict:
        return await handle_list_plugins(manager)

    @tool(
        "remove_plugin",
        "Remove an installed plugin by alias.\n"
        "\n"
        "Parameters:\n"
        "- alias (str, required): The plugin alias to remove.\n"
        "\n"
        "Removes the plugin directory and unregisters its skills.",
        RemovePluginArgs.model_json_schema(),
    )
    async def remove_plugin(args: dict) -> dict:
        try:
            parsed = RemovePluginArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }
        return await handle_remove_plugin(parsed, manager)

    return create_sdk_mcp_server(
        name="plugins",
        version="1.0.0",
        tools=[install_plugin, list_plugins, remove_plugin],
    )

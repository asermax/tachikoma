"""Workspace initialization hook.

Provides the workspace_hook bootstrap function that creates the workspace
root and .tachikoma/ data folder if missing.

See: DLT-023 (Workspace bootstrap), DLT-005 (Hook extraction pattern).
"""

from tachikoma.bootstrap import BootstrapContext


async def workspace_hook(ctx: BootstrapContext) -> None:
    """Create workspace root and .tachikoma/ data folder if missing."""
    settings = ctx.settings_manager.settings
    workspace_path = settings.workspace.path

    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        raise RuntimeError(
            f"Workspace path exists but is not a directory: {workspace_path}"
        )
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create workspace directory: Permission denied: {workspace_path}"
        ) from exc

    try:
        settings.workspace.data_path.mkdir(exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create data directory: Permission denied: {settings.workspace.data_path}"
        ) from exc

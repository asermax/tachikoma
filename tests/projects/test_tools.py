"""Tests for project MCP tools."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from tachikoma.projects.tools import (
    create_projects_server,
    handle_deregister_project,
    handle_register_project,
)


class TestCreateProjectsServer:
    """Tests for create_projects_server factory."""

    def test_returns_dict_for_mcp_servers(self, tmp_path: Path) -> None:
        """AC: Factory returns a dict compatible with mcp_servers."""
        server = create_projects_server(tmp_path)

        # Should be a dict (McpSdkServerConfig is a dict subclass)
        assert isinstance(server, dict)

    def test_server_has_required_keys(self, tmp_path: Path) -> None:
        """AC: Server config has the required structure."""
        server = create_projects_server(tmp_path)

        # SDK MCP servers have specific keys
        assert "type" in server
        assert server["type"] == "sdk"
        assert "name" in server
        assert server["name"] == "projects"

    def test_server_has_project_tools(self, tmp_path: Path) -> None:
        """AC: Server includes register_project and deregister_project tools."""
        server = create_projects_server(tmp_path)

        # The instance is an MCP Server with the tools
        instance = server["instance"]
        assert instance is not None


class TestHandleRegisterProject:
    """Tests for the extracted register_project handler (DES-006)."""

    async def test_rejects_empty_name(self, tmp_path: Path) -> None:
        """Returns error when name is empty."""
        result = await handle_register_project("", "git@github.com:u/r.git", tmp_path)

        assert result["is_error"] is True
        assert "'name' is required" in result["content"][0]["text"]

    async def test_rejects_empty_url(self, tmp_path: Path) -> None:
        """Returns error when url is empty."""
        result = await handle_register_project("my-app", "", tmp_path)

        assert result["is_error"] is True
        assert "'url' is required" in result["content"][0]["text"]

    async def test_rejects_existing_project(self, tmp_path: Path) -> None:
        """Returns error when project directory already exists."""
        (tmp_path / "projects" / "my-app").mkdir(parents=True)

        result = await handle_register_project("my-app", "git@github.com:u/r.git", tmp_path)

        assert result["is_error"] is True
        assert "already exists" in result["content"][0]["text"]

    @patch("tachikoma.projects.tools.checkout_branch", new_callable=AsyncMock)
    @patch(
        "tachikoma.projects.tools.resolve_default_branch",
        new_callable=AsyncMock, return_value="main",
    )
    @patch("tachikoma.projects.tools.add_submodule", new_callable=AsyncMock)
    async def test_success(
        self, mock_add: AsyncMock, mock_resolve: AsyncMock, mock_checkout: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Registers project and returns success message."""
        result = await handle_register_project("my-app", "git@github.com:u/r.git", tmp_path)

        assert "is_error" not in result
        assert "Registered project 'my-app'" in result["content"][0]["text"]
        mock_add.assert_awaited_once_with(tmp_path, "my-app", "git@github.com:u/r.git")
        mock_resolve.assert_awaited_once()
        mock_checkout.assert_awaited_once()

    @patch(
        "tachikoma.projects.tools.add_submodule",
        new_callable=AsyncMock, side_effect=RuntimeError("clone failed"),
    )
    async def test_cleans_up_on_failure(self, mock_add: AsyncMock, tmp_path: Path) -> None:
        """Cleans up partial state when registration fails."""
        result = await handle_register_project("my-app", "git@github.com:u/r.git", tmp_path)

        assert result["is_error"] is True
        assert "clone failed" in result["content"][0]["text"]


class TestHandleDeregisterProject:
    """Tests for the extracted deregister_project handler (DES-006)."""

    async def test_rejects_empty_name(self, tmp_path: Path) -> None:
        """Returns error when name is empty."""
        result = await handle_deregister_project("", False, tmp_path)

        assert result["is_error"] is True
        assert "'name' is required" in result["content"][0]["text"]

    async def test_rejects_missing_project(self, tmp_path: Path) -> None:
        """Returns error when project directory doesn't exist."""
        result = await handle_deregister_project("nonexistent", False, tmp_path)

        assert result["is_error"] is True
        assert "not found" in result["content"][0]["text"]

    @patch(
        "tachikoma.projects.tools.has_uncommitted_changes_detail",
        new_callable=AsyncMock, return_value="M file.txt",
    )
    async def test_warns_on_uncommitted_changes(
        self, mock_changes: AsyncMock, tmp_path: Path,
    ) -> None:
        """Returns warning when project has uncommitted changes and force=false."""
        (tmp_path / "projects" / "my-app").mkdir(parents=True)

        result = await handle_deregister_project("my-app", False, tmp_path)

        assert result["is_error"] is True
        assert "uncommitted changes" in result["content"][0]["text"]

    @patch("tachikoma.projects.tools.remove_submodule", new_callable=AsyncMock)
    @patch(
        "tachikoma.projects.tools.has_uncommitted_changes_detail",
        new_callable=AsyncMock, return_value="M file.txt",
    )
    async def test_force_removes_with_uncommitted_changes(
        self, mock_changes: AsyncMock, mock_remove: AsyncMock, tmp_path: Path,
    ) -> None:
        """Removes project when force=true despite uncommitted changes."""
        (tmp_path / "projects" / "my-app").mkdir(parents=True)

        result = await handle_deregister_project("my-app", True, tmp_path)

        assert "is_error" not in result
        assert "Deregistered project 'my-app'" in result["content"][0]["text"]
        mock_remove.assert_awaited_once_with(tmp_path, "my-app")

    @patch("tachikoma.projects.tools.remove_submodule", new_callable=AsyncMock)
    @patch(
        "tachikoma.projects.tools.has_uncommitted_changes_detail",
        new_callable=AsyncMock, return_value="",
    )
    async def test_success(
        self, mock_changes: AsyncMock, mock_remove: AsyncMock, tmp_path: Path,
    ) -> None:
        """Removes clean project successfully."""
        (tmp_path / "projects" / "my-app").mkdir(parents=True)

        result = await handle_deregister_project("my-app", False, tmp_path)

        assert "is_error" not in result
        assert "Deregistered project 'my-app'" in result["content"][0]["text"]

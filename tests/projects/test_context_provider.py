"""Tests for ProjectsContextProvider."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.projects.context_provider import ProjectsContextProvider


@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.mark.asyncio
class TestProjectsContextProvider:
    """Tests for ProjectsContextProvider."""

    async def test_returns_context_with_projects(self, workspace_path: Path) -> None:
        """Returns ContextResult with project list and mcp_servers when projects exist."""
        provider = ProjectsContextProvider(workspace_path)

        with (
            patch(
                "tachikoma.projects.context_provider.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/my-app"],
            ),
            patch(
                "tachikoma.projects.context_provider.current_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            result = await provider.provide("hello")

        assert result is not None
        assert result.tag == "projects"
        assert "my-app: main" in result.content
        assert result.mcp_servers is not None
        assert "projects" in result.mcp_servers

    async def test_returns_context_without_projects(self, workspace_path: Path) -> None:
        """Returns ContextResult with minimal text when no projects exist."""
        provider = ProjectsContextProvider(workspace_path)

        with patch(
            "tachikoma.projects.context_provider.list_submodules",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await provider.provide("hello")

        assert result is not None
        assert result.tag == "projects"
        assert "No projects registered" in result.content
        # MCP tools should still be available
        assert result.mcp_servers is not None
        assert "projects" in result.mcp_servers

    async def test_reports_commit_hash_for_detached_head(
        self, workspace_path: Path
    ) -> None:
        """Reports commit hash when submodule is in detached HEAD state."""
        provider = ProjectsContextProvider(workspace_path)

        with (
            patch(
                "tachikoma.projects.context_provider.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/my-app"],
            ),
            patch(
                "tachikoma.projects.context_provider.current_branch",
                new_callable=AsyncMock,
                return_value=None,  # Detached HEAD
            ),
            patch(
                "tachikoma.projects.context_provider.current_commit_short",
                new_callable=AsyncMock,
                return_value="abc1234",
            ),
        ):
            result = await provider.provide("hello")

        assert result is not None
        assert "abc1234 (detached)" in result.content

    async def test_excludes_corrupted_projects(self, workspace_path: Path) -> None:
        """Excludes projects that fail to read from context."""
        provider = ProjectsContextProvider(workspace_path)

        call_count = [0]

        async def mock_current_branch(path: Path) -> str | None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("corrupted")
            return "main"

        with (
            patch(
                "tachikoma.projects.context_provider.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/corrupted", "projects/ok"],
            ),
            patch(
                "tachikoma.projects.context_provider.current_branch",
                side_effect=mock_current_branch,
            ),
        ):
            result = await provider.provide("hello")

        assert result is not None
        # Only the ok project should be listed
        assert "ok: main" in result.content
        assert "corrupted" not in result.content

    async def test_always_includes_mcp_servers(self, workspace_path: Path) -> None:
        """Always includes MCP servers regardless of project count."""
        provider = ProjectsContextProvider(workspace_path)

        # Test with no projects
        with patch(
            "tachikoma.projects.context_provider.list_submodules",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await provider.provide("hello")

        assert result is not None
        assert result.mcp_servers is not None
        assert "projects" in result.mcp_servers

        # Test with projects
        with (
            patch(
                "tachikoma.projects.context_provider.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/my-app"],
            ),
            patch(
                "tachikoma.projects.context_provider.current_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            result = await provider.provide("hello")

        assert result is not None
        assert result.mcp_servers is not None
        assert "projects" in result.mcp_servers

    async def test_content_includes_registered_projects_header(
        self, workspace_path: Path
    ) -> None:
        """Content includes 'Registered Projects' header when projects exist."""
        provider = ProjectsContextProvider(workspace_path)

        with (
            patch(
                "tachikoma.projects.context_provider.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/app"],
            ),
            patch(
                "tachikoma.projects.context_provider.current_branch",
                new_callable=AsyncMock,
                return_value="master",
            ),
        ):
            result = await provider.provide("hello")

        assert result is not None
        assert "## Registered Projects" in result.content

    async def test_message_parameter_unused(
        self, workspace_path: Path
    ) -> None:
        """The message parameter is unused - projects context is static."""
        provider = ProjectsContextProvider(workspace_path)

        with (
            patch(
                "tachikoma.projects.context_provider.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/app"],
            ),
            patch(
                "tachikoma.projects.context_provider.current_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
        ):
            result1 = await provider.provide("hello")
            result2 = await provider.provide("completely different message")

        # Both should return the same content (static context)
        assert result1 is not None
        assert result2 is not None
        assert result1.content == result2.content

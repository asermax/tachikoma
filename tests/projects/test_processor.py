"""Tests for ProjectsProcessor post-processor."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.projects.processor import SUBMODULE_COMMIT_PROMPT, ProjectsProcessor


@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock session."""
    session = MagicMock()
    session.id = "test-session"
    return session


@pytest.mark.asyncio
class TestProjectsProcessor:
    """Tests for ProjectsProcessor."""

    async def test_no_op_when_no_submodules(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Completes without error when no submodules exist."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        with patch(
            "tachikoma.projects.processor.list_submodules",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await processor.process(mock_session)  # Should not raise

    async def test_no_op_when_all_submodules_clean(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Skips processing when all submodules are clean."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        with (
            patch(
                "tachikoma.projects.processor.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/a", "projects/b"],
            ),
            patch(
                "tachikoma.projects.processor.is_dirty",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            await processor.process(mock_session)  # Should not raise

    async def test_processes_dirty_submodules(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Spawns agent per dirty submodule."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        with (
            patch(
                "tachikoma.projects.processor.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/dirty", "projects/clean"],
            ),
            patch(
                "tachikoma.projects.processor.is_dirty",
                side_effect=[True, False],  # First dirty, second clean
            ),
            patch(
                "tachikoma.projects.processor.query_and_consume",
                new_callable=AsyncMock,
            ),
            patch(
                "tachikoma.projects.processor.push",
                new_callable=AsyncMock,
            ),
        ):
            await processor.process(mock_session)

    async def test_pushes_after_commit(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Pushes to remote after successful commit."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        with (
            patch(
                "tachikoma.projects.processor.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/my-app"],
            ),
            patch(
                "tachikoma.projects.processor.is_dirty",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tachikoma.projects.processor.query_and_consume",
                new_callable=AsyncMock,
            ),
            patch(
                "tachikoma.projects.processor.push",
                new_callable=AsyncMock,
            ) as mock_push,
        ):
            await processor.process(mock_session)

        mock_push.assert_awaited_once()

    async def test_push_failure_logged_continues(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Push failure is logged but doesn't block other submodules."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        push_call_count = [0]

        async def failing_push(path: Path) -> None:
            push_call_count[0] += 1
            if push_call_count[0] == 1:
                raise RuntimeError("Push failed")

        with (
            patch(
                "tachikoma.projects.processor.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/a", "projects/b"],
            ),
            patch(
                "tachikoma.projects.processor.is_dirty",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tachikoma.projects.processor.query_and_consume",
                new_callable=AsyncMock,
            ),
            patch(
                "tachikoma.projects.processor.push",
                side_effect=failing_push,
            ),
        ):
            await processor.process(mock_session)

        # Both pushes should have been attempted
        assert push_call_count[0] == 2

    async def test_commit_failure_logged_no_push(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Commit failure is logged and push is not attempted."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        with (
            patch(
                "tachikoma.projects.processor.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/my-app"],
            ),
            patch(
                "tachikoma.projects.processor.is_dirty",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tachikoma.projects.processor.query_and_consume",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Commit failed"),
            ),
            patch(
                "tachikoma.projects.processor.push",
                new_callable=AsyncMock,
            ) as mock_push,
        ):
            await processor.process(mock_session)

        # Push should not have been called since commit failed
        mock_push.assert_not_awaited()

    async def test_processes_submodules_in_parallel(
        self, workspace_path: Path, mock_session: MagicMock
    ) -> None:
        """AC: Multiple dirty submodules are processed in parallel."""
        processor = ProjectsProcessor(AgentDefaults(cwd=workspace_path))

        call_order: list[str] = []

        async def track_query_and_consume(*args, **kwargs):
            call_order.append("query_start")
            await asyncio.sleep(0.05)
            call_order.append("query_end")

        async def track_push(path: Path) -> None:
            call_order.append(f"push_{path.name}")

        with (
            patch(
                "tachikoma.projects.processor.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/a", "projects/b"],
            ),
            patch(
                "tachikoma.projects.processor.is_dirty",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tachikoma.projects.processor.query_and_consume",
                side_effect=track_query_and_consume,
            ),
            patch(
                "tachikoma.projects.processor.push",
                side_effect=track_push,
            ),
        ):
            await processor.process(mock_session)

        # Both queries should have started before either finished
        query_starts = [i for i, x in enumerate(call_order) if x == "query_start"]
        query_ends = [i for i, x in enumerate(call_order) if x == "query_end"]
        assert len(query_starts) == 2
        assert len(query_ends) == 2
        # Both starts should come before both ends (parallel execution)
        assert query_starts[1] < query_ends[0]


class TestSubmoduleCommitPrompt:
    """Tests for the SUBMODULE_COMMIT_PROMPT constant."""

    def test_uses_only_allowed_git_commands(self) -> None:
        """AC: Prompt only allows status, diff, log, add, commit."""
        allowed = {"git status", "git diff", "git log", "git add", "git commit"}

        # Check the prompt mentions the allowed commands
        for cmd in allowed:
            assert cmd in SUBMODULE_COMMIT_PROMPT

        # Check the prompt explicitly forbids push
        assert "Do NOT use" in SUBMODULE_COMMIT_PROMPT
        assert "git push" in SUBMODULE_COMMIT_PROMPT

    def test_instructs_to_learn_project_commit_style(self) -> None:
        """AC: Prompt instructs agent to learn project's commit style."""
        assert "git log" in SUBMODULE_COMMIT_PROMPT
        assert "commit style" in SUBMODULE_COMMIT_PROMPT.lower()

    def test_instructs_to_check_project_instructions(self) -> None:
        """AC: Prompt instructs agent to check CONTRIBUTING.md, CLAUDE.md."""
        assert "CONTRIBUTING.md" in SUBMODULE_COMMIT_PROMPT
        assert "CLAUDE.md" in SUBMODULE_COMMIT_PROMPT

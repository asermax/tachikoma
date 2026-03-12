"""Bootstrap module tests.

Tests for DLT-023: Bootstrap agent workspace on first run.
"""

from pathlib import Path

import pytest

from tachikoma.bootstrap import Bootstrap, BootstrapContext, BootstrapError, workspace_hook
from tachikoma.config import SettingsManager


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[workspace]\npath = "{tmp_path / "workspace"}"\n')
    return SettingsManager(config_path)


class TestBootstrap:
    """Tests for the Bootstrap registry class."""

    async def test_runs_hooks_in_registration_order(
        self, settings_manager: SettingsManager
    ) -> None:
        """AC (R5): Hooks execute in registration order."""
        order: list[str] = []

        async def first_hook(ctx):
            order.append("first")

        async def second_hook(ctx):
            order.append("second")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("first", first_hook)
        bootstrap.register("second", second_hook)
        await bootstrap.run()

        assert order == ["first", "second"]

    async def test_no_hooks_completes_successfully(self, settings_manager: SettingsManager) -> None:
        """AC (R2): Zero hooks registered is a valid no-op."""
        bootstrap = Bootstrap(settings_manager)

        await bootstrap.run()

    async def test_wraps_hook_error_in_bootstrap_error(
        self, settings_manager: SettingsManager
    ) -> None:
        """AC (R6): Hook failure raises BootstrapError naming the hook."""
        async def failing_hook(ctx):
            raise ValueError("something broke")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("bad-hook", failing_hook)

        with pytest.raises(BootstrapError, match="bad-hook"):
            await bootstrap.run()

    async def test_error_chains_original_exception(self, settings_manager: SettingsManager) -> None:
        """AC (R6): BootstrapError.__cause__ is the original exception."""
        async def failing_hook(ctx):
            raise ValueError("original")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("fail", failing_hook)

        with pytest.raises(BootstrapError) as exc_info:
            await bootstrap.run()

        assert isinstance(exc_info.value.__cause__, ValueError)

    async def test_stops_on_first_hook_failure(self, settings_manager: SettingsManager) -> None:
        """AC (R6): Hook B never runs if hook A fails."""
        ran: list[str] = []

        async def hook_a(ctx):
            raise ValueError("fail")

        async def hook_b(ctx):
            ran.append("b")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("a", hook_a)
        bootstrap.register("b", hook_b)

        with pytest.raises(BootstrapError):
            await bootstrap.run()

        assert ran == []

    async def test_hook_receives_bootstrap_context(self, settings_manager: SettingsManager) -> None:
        """AC (R2, R3): Hook has access to settings_manager and prompt."""
        captured = {}

        async def capture_hook(ctx):
            captured["settings_manager"] = ctx.settings_manager
            captured["prompt"] = ctx.prompt

        bootstrap = Bootstrap(settings_manager, prompt=lambda q: "answer")
        bootstrap.register("capture", capture_hook)
        await bootstrap.run()

        assert captured["settings_manager"] is settings_manager
        assert captured["prompt"]("question") == "answer"

    async def test_hook_can_prompt_user(self, settings_manager: SettingsManager) -> None:
        """AC (R4): Hook calls ctx.prompt and receives the injected callable's response."""
        responses: list[str] = []

        async def prompting_hook(ctx):
            responses.append(ctx.prompt("Enter value:"))

        bootstrap = Bootstrap(settings_manager, prompt=lambda q: "user-input")
        bootstrap.register("prompter", prompting_hook)
        await bootstrap.run()

        assert responses == ["user-input"]

    async def test_context_extras_writable_by_hooks(
        self, settings_manager: SettingsManager
    ) -> None:
        """AC: Hooks can store objects in ctx.extras for the caller to retrieve."""
        async def storing_hook(ctx):
            ctx.extras["my_object"] = {"key": "value"}

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("storer", storing_hook)
        await bootstrap.run()

        assert bootstrap.extras["my_object"] == {"key": "value"}


class TestWorkspaceHook:
    """Tests for the workspace initialization hook."""

    @pytest.fixture
    def ctx(self, settings_manager: SettingsManager) -> BootstrapContext:
        return BootstrapContext(settings_manager=settings_manager, prompt=input)

    async def test_creates_workspace_and_data_dir(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R0, R1): No dirs exist, both workspace and .tachikoma/ are created."""
        await workspace_hook(ctx)

        ws = settings_manager.settings.workspace

        assert ws.path.is_dir()
        assert ws.data_path.is_dir()

    async def test_skips_when_workspace_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R7): Dirs already exist, no error on re-run."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True)
        ws.data_path.mkdir()

        await workspace_hook(ctx)

        assert ws.path.is_dir()
        assert ws.data_path.is_dir()

    async def test_creates_data_dir_when_workspace_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R1): Workspace exists but .tachikoma/ doesn't, creates it."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True)

        await workspace_hook(ctx)

        assert ws.data_path.is_dir()

    async def test_raises_when_path_is_file(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R9): Workspace path is a file, raises with clear error."""
        ws = settings_manager.settings.workspace
        ws.path.parent.mkdir(parents=True, exist_ok=True)
        ws.path.touch()

        with pytest.raises(RuntimeError, match="not a directory"):
            await workspace_hook(ctx)

    async def test_raises_on_permission_error(self, ctx: BootstrapContext, mocker) -> None:
        """AC (R9): mkdir fails with PermissionError, raises with clear message."""
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError)

        with pytest.raises(RuntimeError, match="Permission denied"):
            await workspace_hook(ctx)

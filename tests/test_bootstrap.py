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

    def test_runs_hooks_in_registration_order(self, settings_manager: SettingsManager) -> None:
        """AC (R5): Hooks execute in registration order."""
        order: list[str] = []

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("first", lambda ctx: order.append("first"))
        bootstrap.register("second", lambda ctx: order.append("second"))
        bootstrap.run()

        assert order == ["first", "second"]

    def test_no_hooks_completes_successfully(self, settings_manager: SettingsManager) -> None:
        """AC (R2): Zero hooks registered is a valid no-op."""
        bootstrap = Bootstrap(settings_manager)

        bootstrap.run()

    def test_wraps_hook_error_in_bootstrap_error(self, settings_manager: SettingsManager) -> None:
        """AC (R6): Hook failure raises BootstrapError naming the hook."""
        def failing_hook(ctx):
            raise ValueError("something broke")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("bad-hook", failing_hook)

        with pytest.raises(BootstrapError, match="bad-hook"):
            bootstrap.run()

    def test_error_chains_original_exception(self, settings_manager: SettingsManager) -> None:
        """AC (R6): BootstrapError.__cause__ is the original exception."""
        def failing_hook(ctx):
            raise ValueError("original")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("fail", failing_hook)

        with pytest.raises(BootstrapError) as exc_info:
            bootstrap.run()

        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_stops_on_first_hook_failure(self, settings_manager: SettingsManager) -> None:
        """AC (R6): Hook B never runs if hook A fails."""
        ran: list[str] = []

        def hook_a(ctx):
            raise ValueError("fail")

        bootstrap = Bootstrap(settings_manager)
        bootstrap.register("a", hook_a)
        bootstrap.register("b", lambda ctx: ran.append("b"))

        with pytest.raises(BootstrapError):
            bootstrap.run()

        assert ran == []

    def test_hook_receives_bootstrap_context(self, settings_manager: SettingsManager) -> None:
        """AC (R2, R3): Hook has access to settings_manager and prompt."""
        captured = {}

        def capture_hook(ctx):
            captured["settings_manager"] = ctx.settings_manager
            captured["prompt"] = ctx.prompt

        bootstrap = Bootstrap(settings_manager, prompt=lambda q: "answer")
        bootstrap.register("capture", capture_hook)
        bootstrap.run()

        assert captured["settings_manager"] is settings_manager
        assert captured["prompt"]("question") == "answer"

    def test_hook_can_prompt_user(self, settings_manager: SettingsManager) -> None:
        """AC (R4): Hook calls ctx.prompt and receives the injected callable's response."""
        responses: list[str] = []

        def prompting_hook(ctx):
            responses.append(ctx.prompt("Enter value:"))

        bootstrap = Bootstrap(settings_manager, prompt=lambda q: "user-input")
        bootstrap.register("prompter", prompting_hook)
        bootstrap.run()

        assert responses == ["user-input"]


class TestWorkspaceHook:
    """Tests for the workspace initialization hook."""

    @pytest.fixture
    def ctx(self, settings_manager: SettingsManager) -> BootstrapContext:
        return BootstrapContext(settings_manager=settings_manager, prompt=input)

    def test_creates_workspace_and_data_dir(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R0, R1): No dirs exist, both workspace and .tachikoma/ are created."""
        workspace_hook(ctx)

        ws = settings_manager.settings.workspace

        assert ws.path.is_dir()
        assert ws.data_path.is_dir()

    def test_skips_when_workspace_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R7): Dirs already exist, no error on re-run."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True)
        ws.data_path.mkdir()

        workspace_hook(ctx)

        assert ws.path.is_dir()
        assert ws.data_path.is_dir()

    def test_creates_data_dir_when_workspace_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R1): Workspace exists but .tachikoma/ doesn't, creates it."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True)

        workspace_hook(ctx)

        assert ws.data_path.is_dir()

    def test_raises_when_path_is_file(
        self, ctx: BootstrapContext, settings_manager: SettingsManager,
    ) -> None:
        """AC (R9): Workspace path is a file, raises with clear error."""
        ws = settings_manager.settings.workspace
        ws.path.parent.mkdir(parents=True, exist_ok=True)
        ws.path.touch()

        with pytest.raises(RuntimeError, match="not a directory"):
            workspace_hook(ctx)

    def test_raises_on_permission_error(self, ctx: BootstrapContext, mocker) -> None:
        """AC (R9): mkdir fails with PermissionError, raises with clear message."""
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError)

        with pytest.raises(RuntimeError, match="Permission denied"):
            workspace_hook(ctx)

"""Bootstrap module tests.

Tests for DLT-023: Bootstrap agent workspace on first run.
"""

import pytest

from tachikoma.bootstrap import Bootstrap, BootstrapError
from tachikoma.config import SettingsManager


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

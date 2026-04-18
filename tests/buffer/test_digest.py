from tachikoma.buffer.digest import build_shutdown_digest
from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority


class TestBuildShutdownDigest:
    def test_preamble_present(self) -> None:
        result = build_shutdown_digest([])

        assert "⟨Shutdown digest⟩" in result
        assert "shutting down" in result

    def test_items_numbered_from_1(self) -> None:
        items = [
            BufferedItem(
                priority=Priority.URGENT,
                prompt="first",
                kind="notification",
                source_id="task:1",
            ),
            BufferedItem(priority=Priority.NORMAL, prompt="second", kind="session_task"),
        ]

        result = build_shutdown_digest(items)

        assert "— Item 1 (urgent, notification, source: task:1) —" in result
        assert "first" in result
        assert "— Item 2 (normal, session_task) —" in result
        assert "second" in result

    def test_source_id_omitted_when_absent(self) -> None:
        item = BufferedItem(priority=Priority.NORMAL, prompt="test", kind="session_task")

        result = build_shutdown_digest([item])

        assert "source:" not in result

    def test_priority_labels_lowercase(self) -> None:
        items = [
            BufferedItem(priority=Priority.URGENT, prompt="a", kind="notification"),
            BufferedItem(priority=Priority.LOW, prompt="b", kind="notification"),
        ]

        result = build_shutdown_digest(items)

        assert "urgent" in result
        assert "low" in result
        assert "URGENT" not in result
        assert "LOW" not in result

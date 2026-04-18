from tachikoma.buffer.priority import Priority


class TestPriority:
    def test_ordering(self) -> None:
        assert Priority.URGENT < Priority.NORMAL < Priority.LOW

    def test_int_values(self) -> None:
        assert int(Priority.URGENT) == 1
        assert int(Priority.NORMAL) == 2
        assert int(Priority.LOW) == 3

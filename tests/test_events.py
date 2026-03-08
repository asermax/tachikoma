from tachikoma.events import Error, Result, TextChunk, ToolActivity


class TestTextChunk:
    def test_stores_text(self) -> None:
        chunk = TextChunk(text="hello world")

        assert chunk.text == "hello world"


class TestToolActivity:
    def test_stores_tool_fields(self) -> None:
        activity = ToolActivity(
            tool_name="Read",
            tool_input={"path": "/tmp/file.txt"},
            result="file contents here",
        )

        assert activity.tool_name == "Read"
        assert activity.tool_input == {"path": "/tmp/file.txt"}
        assert activity.result == "file contents here"


class TestResult:
    def test_stores_session_metadata(self) -> None:
        result = Result(
            session_id="sess-123",
            total_cost_usd=0.05,
            usage={"input_tokens": 100, "output_tokens": 50},
        )

        assert result.session_id == "sess-123"
        assert result.total_cost_usd == 0.05
        assert result.usage == {"input_tokens": 100, "output_tokens": 50}

    def test_fields_default_to_none(self) -> None:
        result = Result()

        assert result.session_id is None
        assert result.total_cost_usd is None
        assert result.usage is None


class TestError:
    def test_stores_error_fields(self) -> None:
        error = Error(message="connection lost", recoverable=True)

        assert error.message == "connection lost"
        assert error.recoverable is True

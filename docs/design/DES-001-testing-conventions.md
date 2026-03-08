# DES-001: Testing Conventions

**Scope**: Python
**Date**: 2026-03-08
**Last Updated**: 2026-03-08
**Related**: ADR-004 (Testing Library)

## Pattern

Follow these testing conventions for all Tachikoma Python code.

### 1. Directory Structure

Mirror the src package structure in tests:

```
src/tachikoma/
├── __init__.py
├── __main__.py
├── coordinator.py
├── events.py
├── adapter.py
└── repl.py

tests/
├── conftest.py              # Root fixtures (shared across all tests)
├── test_events.py           # Unit tests for domain types
├── test_adapter.py          # Unit tests for message adapter
├── test_coordinator.py      # Integration tests for coordinator
└── test_repl.py             # Tests for REPL behavior
```

**When to use unit tests**: Pure functions and data transformations (adapter mapping, event construction, validators). These have no external dependencies and test logic in isolation.

**When to use integration tests**: Components that interact with the SDK or external systems (coordinator). Mock the SDK subprocess, test the full send_message → AgentEvent flow.

**Naming:**
- Test files: `test_<module>.py` (e.g., `test_adapter.py`, `test_coordinator.py`)
- Test classes: `Test<Feature>` (e.g., `TestMessageAdapter`, `TestCoordinatorSendMessage`)
- Test functions: `test_<behavior>` (e.g., `test_maps_text_block_to_text_chunk`)

### 2. Pytest Configuration

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-m 'not slow'"
markers = [
    "slow: marks tests as slow (run with '-m slow' to include)",
]
```

### 3. Fixture Hierarchy

**Root `conftest.py`** — Shared fixtures:
- Mock SDK query function
- Common event builders

**Feature `conftest.py`** — Feature-specific fixtures (when tests grow):
- Mock services
- Test data builders
- Feature-specific setup/teardown

### 4. Async Testing

With `asyncio_mode = "auto"`, no need for `@pytest.mark.asyncio` on individual tests:

```python
class TestCoordinatorSendMessage:
    """Tests for Coordinator.send_message()."""

    async def test_yields_text_chunk_for_assistant_text(
        self, coordinator: Coordinator, mock_query: AsyncMock
    ) -> None:
        """AC: Agent responds with text content."""
        events = [e async for e in coordinator.send_message("hello")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) > 0
        assert text_events[0].text == "Hello!"
```

### 5. Class-Based Test Organization

Group related tests by feature or acceptance criteria:

```python
"""Message adapter tests.

Tests for DLT-001: Core agent architecture.
"""

from tachikoma.adapter import adapt
from tachikoma.events import TextChunk, ToolActivity, Error


class TestAdaptAssistantMessage:
    """Tests for adapting AssistantMessage to AgentEvent."""

    def test_maps_text_block_to_text_chunk(self) -> None:
        """AC: Text content is extracted from AssistantMessage."""
        # ...

    def test_maps_tool_use_block_to_tool_activity(self) -> None:
        """AC: Tool invocations are surfaced as ToolActivity events."""
        # ...
```

### 6. Acceptance Criteria References

Link tests to specs via docstrings:

```python
async def test_preserves_conversation_context(
    self, coordinator: Coordinator
) -> None:
    """AC: Follow-up messages have context from prior messages.

    See: docs/delta-specs/DLT-001.md (R0)
    """
```

### 7. Mocking

#### 7.1 Mock Fixtures

Create reusable mock fixtures with sensible defaults:

```python
@pytest.fixture
def mock_query(mocker: MockerFixture) -> AsyncMock:
    """Mock SDK query() that returns a simple text response."""
    mock = mocker.patch("tachikoma.coordinator.query")

    async def fake_query(*args, **kwargs):
        yield AssistantMessage(content=[TextBlock(type="text", text="Hello!")])
        yield ResultMessage(session_id="test-session", ...)

    mock.side_effect = fake_query
    return mock
```

#### 7.2 Patching with mocker

Use the `mocker` fixture from `pytest-mock` for all patching needs:

```python
def test_handles_cli_not_found(mocker: MockerFixture) -> None:
    """AC: Missing CLI produces clear error."""
    mocker.patch(
        "tachikoma.coordinator.query",
        side_effect=CLINotFoundError("Claude CLI not found"),
    )
    # ...
```

**Key principles**:
- Patch at the location where the dependency is imported, not where it's defined
- The `mocker` fixture automatically undoes patches after each test
- Use `mocker.patch()` instead of `with patch():` context managers or `@patch` decorators
- Use `mocker.spy()` to wrap real objects while tracking calls

### 8. Test Data Helpers

Create helper functions for common test data:

```python
# tests/conftest.py

from claude_code_sdk import AssistantMessage, TextBlock, ToolUseBlock, ResultMessage


def make_text_message(text: str = "Hello!") -> AssistantMessage:
    """Create an AssistantMessage with a single text block."""
    return AssistantMessage(
        role="assistant",
        content=[TextBlock(type="text", text=text)],
    )


def make_tool_message(
    name: str = "Read", input: dict | None = None
) -> AssistantMessage:
    """Create an AssistantMessage with a tool use block."""
    return AssistantMessage(
        role="assistant",
        content=[ToolUseBlock(type="tool_use", id="test-id", name=name, input=input or {})],
    )
```

## Rationale

1. **Mirrored structure**: Finding tests for any module is intuitive
2. **Class organization**: Groups related tests, improves test output readability
3. **AC references**: Traceability from tests to requirements
4. **Fixture hierarchy**: Root fixtures are shared, feature fixtures are isolated
5. **Mock defaults**: Tests focus on the specific behavior being tested
6. **Async-first**: Agent code is async, tests should be too

## Exceptions

1. **Simple utility functions**: Pure functions without async or state may use simple `test_` functions without classes
2. **Complex business rules**: Pure algorithms with significant edge case logic may warrant isolated unit tests

## Related Patterns

- **ADR-004**: Testing library selection (pytest)

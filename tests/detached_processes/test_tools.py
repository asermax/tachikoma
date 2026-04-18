"""Tests for detached process MCP tools."""

import contextlib
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from tachikoma.detached_processes.tools import create_detached_process_tools_server

from .conftest import _make_record

TZ = ZoneInfo("UTC")


def _get_tools(repo, mock_bus, log_dir):
    """Build the tool server and return a dict of tool_name -> handler."""
    server = create_detached_process_tools_server(repo, mock_bus, log_dir, TZ)
    # create_sdk_mcp_server returns a dict; the tools are captured in the closures
    # We need to extract the tool handlers from the @tool decorators
    return server


# ---------------------------------------------------------------------------
# Factory test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_returns_server(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")

    server = create_detached_process_tools_server(repo, mock_bus, log_dir, TZ)

    assert server is not None
    assert server["name"] == "detached-process-tools"


# ---------------------------------------------------------------------------
# start_process tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_process_rejects_empty_name(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    create_detached_process_tools_server(repo, mock_bus, log_dir, TZ)

    # Test through the SdkMcpTool handlers — re-extract via the closures
    tools_map = _build_tools_map(repo, mock_bus, log_dir)
    result = await tools_map["start_process"]({"name": "  ", "command": "echo hi"})

    assert result["is_error"] is True
    assert "name" in result["content"][0]["text"]


def _build_tools_map(repo, mock_bus, log_dir):
    """Build tools server and return {name: handler_fn} dict."""

    # Capture the tools registered during factory creation
    captured = {}

    class FakeTool:
        def __init__(self, name, desc, schema):
            self.name = name
            self.description = desc
            self.input_schema = schema
            self._handler = None

        def __call__(self, fn):
            self._handler = fn
            captured[self.name] = fn
            return self

    with patch("tachikoma.detached_processes.tools.tool", FakeTool):
        create_detached_process_tools_server(repo, mock_bus, log_dir, TZ)

    return captured


# Use a fixture-based approach instead — create the tools map per test

@pytest.fixture
def tools_map(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    return _build_tools_map(repo, mock_bus, log_dir), AsyncMock(), Path("/tmp/test-logs")


# Re-do the tests using direct handler invocation via the captured tools

@pytest.mark.asyncio
async def test_start_process_rejects_empty_name_v2(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["start_process"]({"name": "  ", "command": "echo hi"})

    assert result["is_error"] is True
    assert "name" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_start_process_rejects_empty_command(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["start_process"]({"name": "test", "command": "  "})

    assert result["is_error"] is True
    assert "command" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_start_process_allows_duplicate_names(tmp_path, repo):
    """R16: names are non-unique display labels."""
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result1 = await tools["start_process"]({"name": "same-name", "command": "sleep 5"})
    result2 = await tools["start_process"]({"name": "same-name", "command": "sleep 5"})

    assert result1.get("is_error") is not True
    assert result2.get("is_error") is not True

    running = await repo.list_running()
    assert len(running) == 2

    for r in running:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(r.pid), signal.SIGKILL)


# ---------------------------------------------------------------------------
# list_processes tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_running_processes(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="lp1", status="running"))
    await repo.create(_make_record(record_id="lp2", status="exited", exit_code=0))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=True):
        result = await tools["list_processes"]({})

    text = result["content"][0]["text"]
    assert "lp1" in text
    assert "lp2" not in text


@pytest.mark.asyncio
async def test_list_exited_processes(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="ex1", status="exited", exit_code=0))

    result = await tools["list_processes"]({"archived": True})

    text = result["content"][0]["text"]
    assert "ex1" in text


@pytest.mark.asyncio
async def test_list_empty_returns_message(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["list_processes"]({})

    text = result["content"][0]["text"]
    assert "no running processes found" in text.lower()


# ---------------------------------------------------------------------------
# get_process tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_process_found(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="gp1", status="exited", exit_code=0))

    result = await tools["get_process"]({"process_id": "gp1"})

    text = result["content"][0]["text"]
    assert "gp1" in text
    assert "exited" in text


@pytest.mark.asyncio
async def test_get_process_not_found(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["get_process"]({"process_id": "nonexistent"})

    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# read_process_output tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_output_with_content(repo, tmp_path):
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    log_path = log_dir / "ro1.log"
    log_path.write_text("line 1\nline 2\nline 3\n")

    await repo.create(_make_record(record_id="ro1", status="running", log_path=str(log_path)))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=True):
        result = await tools["read_process_output"]({"process_id": "ro1"})

    text = result["content"][0]["text"]
    assert "line 1" in text
    assert "line 3" in text


@pytest.mark.asyncio
async def test_read_output_empty_log(repo, tmp_path):
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    log_path = log_dir / "ro2.log"
    log_path.write_text("")

    await repo.create(_make_record(record_id="ro2", status="running", log_path=str(log_path)))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=True):
        result = await tools["read_process_output"]({"process_id": "ro2"})

    text = result["content"][0]["text"]
    assert "no output yet" in text.lower()


@pytest.mark.asyncio
async def test_read_output_missing_log(repo, tmp_path):
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(
        _make_record(record_id="ro3", status="running", log_path=str(log_dir / "ro3.log"))
    )

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=True):
        result = await tools["read_process_output"]({"process_id": "ro3"})

    assert result["content"][0]["text"] == "No output yet."


# ---------------------------------------------------------------------------
# stop_process tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_already_dead(repo, tmp_path):
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="sp1", status="running"))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=False):
        result = await tools["stop_process"]({"process_id": "sp1"})

    text = result["content"][0]["text"]
    assert "already stopped" in text.lower()


@pytest.mark.asyncio
async def test_stop_not_found(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["stop_process"]({"process_id": "nonexistent"})

    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_stop_unknown_signal(repo, tmp_path):
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="sp2", status="running"))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=True):
        result = await tools["stop_process"]({"process_id": "sp2", "signal": "SIGFAKE"})

    assert result["is_error"] is True
    assert "Unknown signal" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# rename_process tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_process(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="rn1"))

    result = await tools["rename_process"]({"process_id": "rn1", "name": "New Name"})

    assert "New Name" in result["content"][0]["text"]

    updated = await repo.get("rn1")
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_rename_empty_name_rejected(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["rename_process"]({"process_id": "rn2", "name": "  "})

    assert result["is_error"] is True
    text = result["content"][0]["text"].lower()
    assert "empty" in text or "whitespace" in text


@pytest.mark.asyncio
async def test_rename_not_found(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    result = await tools["rename_process"]({"process_id": "nonexistent", "name": "test"})

    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"]

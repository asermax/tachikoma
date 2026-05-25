"""Tests for detached process MCP tools."""

import contextlib
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from tachikoma.detached_processes.model import STOP_REASON_AGENT_STOPPED
from tachikoma.detached_processes.tools import create_detached_process_tools_server

from .conftest import _make_record

TZ = ZoneInfo("UTC")


def _build_tools_map(repo, mock_bus, log_dir, **cgroup_kwargs):
    """Build tools server and return {name: handler_fn} dict by capturing
    the handlers registered with @tool during the factory call."""
    captured: dict = {}

    class FakeTool:
        def __init__(self, name, desc, schema):
            self.name = name
            self.description = desc
            self.input_schema = schema

        def __call__(self, fn):
            captured[self.name] = fn
            return self

    with patch("tachikoma.detached_processes.tools.tool", FakeTool):
        create_detached_process_tools_server(repo, mock_bus, log_dir, TZ, **cgroup_kwargs)

    return captured


@pytest.mark.asyncio
async def test_factory_returns_server(repo):
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")

    server = create_detached_process_tools_server(repo, mock_bus, log_dir, TZ)

    assert server is not None
    assert server["name"] == "detached-process-tools"


@pytest.mark.asyncio
async def test_start_process_rejects_empty_name(repo):
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


# ---------------------------------------------------------------------------
# stop_process + stop_reason tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_marks_agent_stopped_before_signalling(repo, tmp_path):
    """stop_process sets stop_reason='agent_stopped' before signalling."""
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="sp-flag", status="running"))

    with (
        patch("tachikoma.detached_processes.tools.is_alive", return_value=True),
        patch("tachikoma.detached_processes.tools.terminate"),
    ):
        result = await tools["stop_process"]({"process_id": "sp-flag"})

    assert result.get("is_error") is not True

    updated = await repo.get("sp-flag")
    assert updated is not None
    assert updated.stop_reason == STOP_REASON_AGENT_STOPPED


@pytest.mark.asyncio
async def test_stop_permission_error_clears_flag(repo, tmp_path):
    """AC4: PermissionError during terminate clears stop_reason."""
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="sp-perm", status="running"))

    with (
        patch("tachikoma.detached_processes.tools.is_alive", return_value=True),
        patch("tachikoma.detached_processes.tools.terminate", side_effect=PermissionError),
    ):
        result = await tools["stop_process"]({"process_id": "sp-perm"})

    assert result["is_error"] is True
    assert "Permission denied" in result["content"][0]["text"]

    updated = await repo.get("sp-perm")
    assert updated is not None
    assert updated.stop_reason is None


@pytest.mark.asyncio
async def test_stop_already_dead_does_not_set_stop_reason(repo, tmp_path):
    """AC5: Already-dead process does NOT get stop_reason set."""
    mock_bus = AsyncMock()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="sp-dead", status="running"))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=False):
        result = await tools["stop_process"]({"process_id": "sp-dead"})

    text = result["content"][0]["text"]
    assert "already stopped" in text.lower()

    updated = await repo.get("sp-dead")
    assert updated is not None
    assert updated.stop_reason is None


# ---------------------------------------------------------------------------
# Memory usage display tests (get_process)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_process_shows_memory_usage(repo):
    """Running process with cgroup shows memory usage and limit."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(
        _make_record(
            record_id="mu1",
            status="running",
            cgroup_path="/sys/fs/cgroup/test-mu1",
            memory_limit=512 * 1024 * 1024,  # 512MB
        )
    )

    with (
        patch("tachikoma.detached_processes.tools.is_alive", return_value=True),
        patch(
            "tachikoma.detached_processes.tools.read_memory_current",
            return_value=256 * 1024 * 1024,
        ),
    ):
        result = await tools["get_process"]({"process_id": "mu1"})

    text = result["content"][0]["text"]
    assert "Memory usage: 256MB" in text
    assert "Memory limit: 512MB" in text


@pytest.mark.asyncio
async def test_get_process_no_cgroup_no_memory_fields(repo):
    """Process without cgroup omits memory usage fields."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(_make_record(record_id="mu2", status="running"))

    with patch("tachikoma.detached_processes.tools.is_alive", return_value=True):
        result = await tools["get_process"]({"process_id": "mu2"})

    text = result["content"][0]["text"]
    assert "Memory usage" not in text
    assert "Memory limit" not in text


@pytest.mark.asyncio
async def test_get_process_memory_read_fails_shows_limit_only(repo):
    """Memory read fails but limit is still shown."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(repo, mock_bus, log_dir)

    await repo.create(
        _make_record(
            record_id="mu3",
            status="running",
            cgroup_path="/sys/fs/cgroup/test-mu3",
            memory_limit=1024 * 1024 * 1024,  # 1GB
        )
    )

    with (
        patch("tachikoma.detached_processes.tools.is_alive", return_value=True),
        patch("tachikoma.detached_processes.tools.read_memory_current", return_value=None),
    ):
        result = await tools["get_process"]({"process_id": "mu3"})

    text = result["content"][0]["text"]
    assert "Memory usage" not in text
    assert "Memory limit: 1024MB" in text


# ---------------------------------------------------------------------------
# memory_limit_mb validation tests (start_process)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_process_rejects_zero_memory_limit(repo):
    """memory_limit_mb=0 returns validation error."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=True,
        cgroup_parent_path="/sys/fs/cgroup",
        default_memory_limit_mb=1024,
    )

    result = await tools["start_process"](
        {"name": "test", "command": "echo hi", "memory_limit_mb": 0}
    )

    assert result["is_error"] is True
    assert "Minimum value is 1" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_start_process_rejects_negative_memory_limit(repo):
    """Negative memory_limit_mb returns validation error."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=True,
        cgroup_parent_path="/sys/fs/cgroup",
        default_memory_limit_mb=1024,
    )

    result = await tools["start_process"](
        {"name": "test", "command": "echo hi", "memory_limit_mb": -5}
    )

    assert result["is_error"] is True
    assert "Minimum value is 1" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_start_process_rejects_memory_limit_exceeding_ram(repo, mocker):
    """memory_limit_mb exceeding system RAM returns validation error."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=True,
        cgroup_parent_path="/sys/fs/cgroup",
        default_memory_limit_mb=1024,
    )

    mock_ram = mocker.patch("tachikoma.detached_processes.tools.psutil")
    mock_ram.virtual_memory().total = 1024 * 1024 * 1024  # 1GB

    result = await tools["start_process"](
        {"name": "test", "command": "echo hi", "memory_limit_mb": 2048}
    )

    assert result["is_error"] is True
    assert "exceeds system RAM" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_start_process_no_cgroup_uses_default_limit(repo):
    """When cgroup_available=False, no cgroup params are passed to spawn."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=False,
        default_memory_limit_mb=1024,
    )

    with patch("tachikoma.detached_processes.tools.spawn_process") as mock_spawn:
        mock_spawn.return_value = _make_record(record_id="sp-nocg")
        result = await tools["start_process"]({"name": "test", "command": "echo hi"})

    assert result.get("is_error") is not True
    _, kwargs = mock_spawn.call_args
    assert kwargs["memory_limit_bytes"] is None
    assert kwargs["cgroup_parent_path"] is None


@pytest.mark.asyncio
async def test_start_process_no_default_no_per_process_no_cgroup(repo):
    """No config default and no per-process limit means no cgroup, even when available."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=True,
        cgroup_parent_path="/sys/fs/cgroup",
        default_memory_limit_mb=None,
    )

    with patch("tachikoma.detached_processes.tools.spawn_process") as mock_spawn:
        mock_spawn.return_value = _make_record(record_id="sp-no-default")
        result = await tools["start_process"]({"name": "test", "command": "echo hi"})

    assert result.get("is_error") is not True
    _, kwargs = mock_spawn.call_args
    assert kwargs["memory_limit_bytes"] is None
    assert kwargs["cgroup_parent_path"] is None


@pytest.mark.asyncio
async def test_start_process_cgroup_available_passes_limit(repo):
    """When cgroup_available=True, effective limit is converted to bytes."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=True,
        cgroup_parent_path="/sys/fs/cgroup",
        default_memory_limit_mb=512,
    )

    with patch("tachikoma.detached_processes.tools.spawn_process") as mock_spawn:
        mock_spawn.return_value = _make_record(record_id="sp-cg")
        result = await tools["start_process"]({"name": "test", "command": "echo hi"})

    assert result.get("is_error") is not True
    _, kwargs = mock_spawn.call_args
    assert kwargs["memory_limit_bytes"] == 512 * 1024 * 1024
    assert kwargs["cgroup_parent_path"] == "/sys/fs/cgroup"


@pytest.mark.asyncio
async def test_start_process_per_process_override(repo, mocker):
    """Per-process memory_limit_mb overrides config default."""
    mock_bus = AsyncMock()
    log_dir = Path("/tmp/test-logs")
    tools = _build_tools_map(
        repo,
        mock_bus,
        log_dir,
        cgroup_available=True,
        cgroup_parent_path="/sys/fs/cgroup",
        default_memory_limit_mb=1024,
    )

    # Set system RAM high enough so 2048 is accepted
    mock_ram = mocker.patch("tachikoma.detached_processes.tools.psutil")
    mock_ram.virtual_memory().total = 8192 * 1024 * 1024  # 8GB

    with patch("tachikoma.detached_processes.tools.spawn_process") as mock_spawn:
        mock_spawn.return_value = _make_record(record_id="sp-override")
        result = await tools["start_process"](
            {"name": "test", "command": "echo hi", "memory_limit_mb": 2048}
        )

    assert result.get("is_error") is not True
    _, kwargs = mock_spawn.call_args
    assert kwargs["memory_limit_bytes"] == 2048 * 1024 * 1024

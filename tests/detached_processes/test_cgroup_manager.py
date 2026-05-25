"""Tests for cgroup v2 lifecycle operations."""

from pathlib import Path

from tachikoma.detached_processes.cgroup_manager import (
    assign_pid,
    check_oom_kill,
    cleanup_cgroup,
    create_process_cgroup,
    discover_parent_cgroup_path,
    probe_cgroup_support,
    read_memory_current,
)

# ---------------------------------------------------------------------------
# probe_cgroup_support
# ---------------------------------------------------------------------------


def test_probe_support_succeeds(mocker):
    """Returns True when /sys/fs/cgroup is a dir and memory controller listed."""
    mocker.patch.object(Path, "is_dir", return_value=True)
    mock_read = mocker.patch.object(Path, "read_text")
    mock_read.return_value = "cpuset cpu io memory hugetlb\n"
    assert probe_cgroup_support() is True


def test_probe_support_mount_missing(mocker):
    """Returns False when /sys/fs/cgroup is not a directory."""
    mocker.patch.object(Path, "is_dir", return_value=False)
    assert probe_cgroup_support() is False


def test_probe_support_no_memory_controller(mocker):
    """Returns False when memory is not in the controllers list."""
    mocker.patch.object(Path, "is_dir", return_value=True)
    mock_read = mocker.patch.object(Path, "read_text")
    mock_read.return_value = "cpuset cpu io\n"
    assert probe_cgroup_support() is False


def test_probe_support_oserror(mocker):
    """Returns False on OSError reading controllers."""
    mocker.patch.object(Path, "is_dir", return_value=True)
    mocker.patch.object(Path, "read_text", side_effect=OSError("permission denied"))
    assert probe_cgroup_support() is False


# ---------------------------------------------------------------------------
# discover_parent_cgroup_path
# ---------------------------------------------------------------------------

PROC_CGROUP_V2 = "0::/user.slice/user-1000.slice/session-1.scope\n"
PROC_CGROUP_NO_V2 = "1:name=systemd:/\n"


def _setup_proc_self_cgroup(mocker, content):
    """Mock /proc/self/cgroup to return given content."""
    mocker.patch(
        "tachikoma.detached_processes.cgroup_manager.PROC_SELF_CGROUP",
        new=Path("/proc/self/cgroup"),
    )
    mock_path = mocker.patch("pathlib.Path.read_text")
    # First call reads /proc/self/cgroup
    mock_path.return_value = content


def test_discover_finds_v2_path(mocker):
    """Parses 0:: line and returns absolute path."""
    mocker.patch(
        "tachikoma.detached_processes.cgroup_manager.PROC_SELF_CGROUP",
        new=Path("/proc/self/cgroup"),
    )
    # read_text is called on /proc/self/cgroup only
    mocker.patch("pathlib.Path.read_text", return_value=PROC_CGROUP_V2)
    mocker.patch.object(Path, "is_dir", return_value=True)
    mocker.patch.object(Path, "exists", return_value=True)

    result = discover_parent_cgroup_path()
    assert result == "/sys/fs/cgroup/user.slice/user-1000.slice/session-1.scope"


def test_discover_no_v2_entry(mocker):
    """Returns None when no 0:: line exists."""
    mocker.patch(
        "tachikoma.detached_processes.cgroup_manager.PROC_SELF_CGROUP",
        new=Path("/proc/self/cgroup"),
    )
    mocker.patch("pathlib.Path.read_text", return_value=PROC_CGROUP_NO_V2)
    result = discover_parent_cgroup_path()
    assert result is None


def test_discover_oserror_reading_proc(mocker):
    """Returns None when /proc/self/cgroup is unreadable."""
    mocker.patch(
        "tachikoma.detached_processes.cgroup_manager.PROC_SELF_CGROUP",
        new=Path("/proc/self/cgroup"),
    )
    mocker.patch("pathlib.Path.read_text", side_effect=OSError("no such file"))
    result = discover_parent_cgroup_path()
    assert result is None


def test_discover_cgroup_dir_not_found(mocker):
    """Returns None when resolved cgroup path is not a directory."""
    mocker.patch(
        "tachikoma.detached_processes.cgroup_manager.PROC_SELF_CGROUP",
        new=Path("/proc/self/cgroup"),
    )
    mocker.patch("pathlib.Path.read_text", return_value=PROC_CGROUP_V2)
    mocker.patch.object(Path, "is_dir", return_value=False)

    result = discover_parent_cgroup_path()
    assert result is None


def test_discover_no_cgroup_procs(mocker):
    """Returns None when cgroup.procs does not exist."""
    mocker.patch(
        "tachikoma.detached_processes.cgroup_manager.PROC_SELF_CGROUP",
        new=Path("/proc/self/cgroup"),
    )
    mocker.patch("pathlib.Path.read_text", return_value=PROC_CGROUP_V2)
    mocker.patch.object(Path, "is_dir", return_value=True)
    mocker.patch.object(Path, "exists", return_value=False)

    result = discover_parent_cgroup_path()
    assert result is None


# ---------------------------------------------------------------------------
# create_process_cgroup
# ---------------------------------------------------------------------------


def test_create_succeeds(mocker):
    """Creates cgroup dir, writes memory.max, returns path."""
    mock_mkdir = mocker.patch.object(Path, "mkdir")
    mock_write = mocker.patch.object(Path, "write_text")

    result = create_process_cgroup("/sys/fs/cgroup/user.slice", "abc-123", 1073741824)

    assert result == "/sys/fs/cgroup/user.slice/tachikoma-abc-123"
    mock_mkdir.assert_called_once()
    mock_write.assert_called_once_with("1073741824")


def test_create_mkdir_fails(mocker):
    """Returns None when mkdir raises OSError."""
    mocker.patch.object(Path, "mkdir", side_effect=OSError("permission denied"))
    result = create_process_cgroup("/sys/fs/cgroup", "abc", 1024)
    assert result is None


def test_create_write_max_fails(mocker):
    """Returns None and cleans up dir when writing memory.max fails."""
    mocker.patch.object(Path, "mkdir")
    mocker.patch.object(Path, "write_text", side_effect=OSError("read-only"))
    mock_rmdir = mocker.patch.object(Path, "rmdir")

    result = create_process_cgroup("/sys/fs/cgroup", "abc", 1024)
    assert result is None
    mock_rmdir.assert_called_once()


def test_create_write_max_fails_rmdir_also_fails(mocker):
    """Returns None even when cleanup rmdir also fails."""
    mocker.patch.object(Path, "mkdir")
    mocker.patch.object(Path, "write_text", side_effect=OSError("read-only"))
    mocker.patch.object(Path, "rmdir", side_effect=OSError("not empty"))

    result = create_process_cgroup("/sys/fs/cgroup", "abc", 1024)
    assert result is None


# ---------------------------------------------------------------------------
# assign_pid
# ---------------------------------------------------------------------------


def test_assign_succeeds(mocker):
    """Writes PID to cgroup.procs and returns True."""
    mock_write = mocker.patch.object(Path, "write_text")

    result = assign_pid("/sys/fs/cgroup/tachikoma-abc", 12345)

    assert result is True
    mock_write.assert_called_once_with("12345")


def test_assign_oserror(mocker):
    """Returns False on OSError writing to cgroup.procs."""
    mocker.patch.object(Path, "write_text", side_effect=OSError("denied"))
    assert assign_pid("/sys/fs/cgroup/tachikoma-abc", 12345) is False


# ---------------------------------------------------------------------------
# read_memory_current
# ---------------------------------------------------------------------------


def test_read_memory_succeeds(mocker):
    """Parses memory.current value and returns as int."""
    mocker.patch.object(Path, "read_text", return_value="536870912\n")
    assert read_memory_current("/sys/fs/cgroup/tachikoma-abc") == 536870912


def test_read_memory_oserror(mocker):
    """Returns None on OSError."""
    mocker.patch.object(Path, "read_text", side_effect=OSError("no file"))
    assert read_memory_current("/sys/fs/cgroup/tachikoma-abc") is None


def test_read_memory_non_numeric(mocker):
    """Returns None when content is not a valid integer."""
    mocker.patch.object(Path, "read_text", return_value="not_a_number\n")
    assert read_memory_current("/sys/fs/cgroup/tachikoma-abc") is None


# ---------------------------------------------------------------------------
# check_oom_kill
# ---------------------------------------------------------------------------

EVENTS_NO_OOM = "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n"
EVENTS_OOM = "low 0\nhigh 0\nmax 0\noom 1\noom_kill 1\n"
EVENTS_NO_OOM_KILL_KEY = "low 0\nhigh 0\nmax 0\n"


def test_oom_kill_detected(mocker):
    """Returns True when oom_kill > 0."""
    mocker.patch.object(Path, "read_text", return_value=EVENTS_OOM)
    assert check_oom_kill("/sys/fs/cgroup/tachikoma-abc") is True


def test_oom_kill_not_detected(mocker):
    """Returns False when oom_kill == 0."""
    mocker.patch.object(Path, "read_text", return_value=EVENTS_NO_OOM)
    assert check_oom_kill("/sys/fs/cgroup/tachikoma-abc") is False


def test_oom_kill_key_missing(mocker):
    """Returns False when oom_kill key is absent (defaults to 0 per kernel)."""
    mocker.patch.object(Path, "read_text", return_value=EVENTS_NO_OOM_KILL_KEY)
    assert check_oom_kill("/sys/fs/cgroup/tachikoma-abc") is False


def test_oom_kill_unreadable(mocker):
    """Returns None on OSError."""
    mocker.patch.object(Path, "read_text", side_effect=OSError("no file"))
    assert check_oom_kill("/sys/fs/cgroup/tachikoma-abc") is None


def test_oom_kill_bad_value(mocker):
    """Returns None when oom_kill value is not parseable."""
    mocker.patch.object(Path, "read_text", return_value="oom_kill not_a_number\n")
    assert check_oom_kill("/sys/fs/cgroup/tachikoma-abc") is None


# ---------------------------------------------------------------------------
# cleanup_cgroup
# ---------------------------------------------------------------------------


def test_cleanup_succeeds(mocker):
    """Calls os.rmdir without error."""
    mock_rmdir = mocker.patch("os.rmdir")
    cleanup_cgroup("/sys/fs/cgroup/tachikoma-abc")
    mock_rmdir.assert_called_once_with("/sys/fs/cgroup/tachikoma-abc")


def test_cleanup_oserror_does_not_raise(mocker):
    """Logs warning but does not raise on rmdir failure."""
    mocker.patch("os.rmdir", side_effect=OSError("not empty"))
    cleanup_cgroup("/sys/fs/cgroup/tachikoma-abc")
    # No exception = success

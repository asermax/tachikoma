"""Cgroup v2 lifecycle operations for detached process memory limiting.

All functions are stateless and return None/False on failure — never raise.
Callers log warnings and proceed without cgroup functionality.
"""

import contextlib
import os
from pathlib import Path

from loguru import logger

_log = logger.bind(component="cgroup_manager")

CGROUP_MOUNT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")


def probe_cgroup_support() -> bool:
    """Check whether cgroup v2 with the memory controller is available.

    Verifies /sys/fs/cgroup/ is mounted and the memory controller is listed
    in cgroup.controllers. Returns False on any failure.
    """
    try:
        if not CGROUP_MOUNT.is_dir():
            _log.warning("Cgroup v2 mount not found at {path}", path=str(CGROUP_MOUNT))
            return False

        controllers = (CGROUP_MOUNT / "cgroup.controllers").read_text().strip()
        if "memory" not in controllers.split():
            _log.warning("Memory controller not available in cgroup v2")
            return False

        return True
    except OSError:
        _log.exception("Failed to probe cgroup v2 support")
        return False


def discover_parent_cgroup_path() -> str | None:
    """Discover the cgroup v2 path for the current process.

    Reads /proc/self/cgroup, finds the v2 entry (0::...), resolves it
    to an absolute path under /sys/fs/cgroup, and verifies the directory
    exists with writable cgroup.procs.
    """
    try:
        content = PROC_SELF_CGROUP.read_text().strip()
    except OSError:
        _log.exception("Failed to read {path}", path=str(PROC_SELF_CGROUP))
        return None

    v2_path: str | None = None
    for line in content.splitlines():
        if line.startswith("0::"):
            v2_path = line[3:]
            break

    if v2_path is None:
        _log.warning("No cgroup v2 entry found in {path}", path=str(PROC_SELF_CGROUP))
        return None

    if not v2_path.startswith("/"):
        v2_path = "/" + v2_path

    abs_path = CGROUP_MOUNT / v2_path.lstrip("/")

    try:
        if not abs_path.is_dir():
            _log.warning("Cgroup path does not exist: {path}", path=str(abs_path))
            return None

        procs = abs_path / "cgroup.procs"
        if not procs.exists():
            _log.warning("cgroup.procs not found at {path}", path=str(procs))
            return None

        return str(abs_path)
    except OSError:
        _log.exception("Failed to verify cgroup path at {path}", path=str(abs_path))
        return None


def create_process_cgroup(parent_path: str, record_id: str, memory_limit_bytes: int) -> str | None:
    """Create a per-process cgroup with a memory hard limit.

    Creates {parent_path}/tachikoma-{record_id}, writes memory.max,
    and returns the cgroup path. Returns None on any failure.
    """
    cgroup_path = Path(parent_path) / f"tachikoma-{record_id}"

    try:
        cgroup_path.mkdir()
    except OSError:
        _log.exception("Failed to create cgroup directory: {path}", path=str(cgroup_path))
        return None

    try:
        (cgroup_path / "memory.max").write_text(str(memory_limit_bytes))
    except OSError:
        _log.exception("Failed to set memory.max on cgroup: {path}", path=str(cgroup_path))
        # Best-effort cleanup of the empty directory we just created.
        with contextlib.suppress(OSError):
            cgroup_path.rmdir()
        return None

    return str(cgroup_path)


def assign_pid(cgroup_path: str, pid: int) -> bool:
    """Move a process into its cgroup by writing the PID to cgroup.procs.

    Returns True on success, False on any failure.
    """
    try:
        procs_file = Path(cgroup_path) / "cgroup.procs"
        procs_file.write_text(str(pid))
        return True
    except OSError:
        _log.exception(
            "Failed to assign pid={pid} to cgroup: {path}",
            pid=pid,
            path=cgroup_path,
        )
        return False


def read_memory_current(cgroup_path: str) -> int | None:
    """Read current memory usage from a cgroup's memory.current.

    Returns usage in bytes, or None on any failure.
    """
    try:
        content = (Path(cgroup_path) / "memory.current").read_text().strip()
        return int(content)
    except (OSError, ValueError):
        _log.exception("Failed to read memory.current from cgroup: {path}", path=cgroup_path)
        return None


def check_oom_kill(cgroup_path: str) -> bool | None:
    """Check whether the OOM killer has terminated a process in this cgroup.

    Reads memory.events and checks the oom_kill counter. Returns True if
    oom_kill > 0, False if 0, None if the file is unreadable.
    """
    try:
        content = (Path(cgroup_path) / "memory.events").read_text().strip()
    except OSError:
        _log.exception("Failed to read memory.events from cgroup: {path}", path=cgroup_path)
        return None

    for line in content.splitlines():
        if line.startswith("oom_kill "):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) > 0
                except ValueError:
                    _log.warning(
                        "Unparseable oom_kill value in memory.events: {line}",
                        line=line,
                    )
                    return None

    # oom_kill key not found — defaults to 0 per kernel semantics.
    return False


def cleanup_cgroup(cgroup_path: str) -> None:
    """Remove an empty cgroup directory. Never raises.

    Uses os.rmdir which only succeeds on empty directories (no children,
    no live processes). Logs warning on failure.
    """
    try:
        os.rmdir(cgroup_path)
    except OSError:
        _log.warning("Failed to clean up cgroup directory: {path}", path=cgroup_path)

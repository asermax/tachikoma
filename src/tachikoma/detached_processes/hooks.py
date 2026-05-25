"""Bootstrap hook for the detached processes subsystem.

Creates the log directory, instantiates the repository, probes cgroup v2
availability, and runs crash recovery for any records still marked as running.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.database import Database
from tachikoma.detached_processes.cgroup_manager import (
    discover_parent_cgroup_path,
    probe_cgroup_support,
)
from tachikoma.detached_processes.reconcile import reconcile_exit
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.detached_processes.spawn import is_alive

_log = logger.bind(component="detached_processes")


async def detached_processes_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize detached process repository and run crash recovery.

    Retrieves the shared Database from ctx.extras, creates the
    ProcessRepository, probes cgroup v2 availability, and reconciles
    any running records whose processes are no longer alive.
    Stores the repository, log directory path, and cgroup availability
    in ctx.extras.

    Keys written to ctx.extras:
        "process_repository" -> ProcessRepository instance
        "detached_process_log_dir" -> Path to the log directory
        "cgroup_available" -> bool indicating cgroup v2 availability
        "cgroup_parent_path" -> str | None parent cgroup path
    """
    _log.info("Detached processes hook started")

    database: Database = ctx.extras["database"]
    workspace_path = ctx.settings_manager.settings.workspace.path
    log_dir = workspace_path / ".tachikoma" / "detached-processes"

    log_dir.mkdir(parents=True, exist_ok=True)

    repository = ProcessRepository(database.session_factory)

    # Probe cgroup v2 availability once at bootstrap
    cgroup_available = probe_cgroup_support()
    cgroup_parent_path: str | None = None
    if cgroup_available:
        cgroup_parent_path = discover_parent_cgroup_path()
        if cgroup_parent_path is None:
            cgroup_available = False
            _log.warning("cgroups v2 mounted but could not discover parent cgroup path")
        else:
            _log.info(
                "cgroups v2 available, parent path: {path}",
                path=cgroup_parent_path,
            )
    else:
        _log.info("cgroups v2 not available, processes will run without memory limits")

    # Crash recovery: reconcile records whose processes died while we were
    # down. Notifications suppressed so the user doesn't get a burst on restart.
    running = await repository.list_running()
    for record in running:
        if not is_alive(record):
            await reconcile_exit(
                record.id,
                repository=repository,
                bus=None,
                log_dir=log_dir,
                dispatch_notification=False,
            )
            _log.info(
                "Crash recovery: marked process {id} as exited",
                id=record.id,
            )

    ctx.extras["process_repository"] = repository
    ctx.extras["detached_process_log_dir"] = log_dir
    ctx.extras["cgroup_available"] = cgroup_available
    ctx.extras["cgroup_parent_path"] = cgroup_parent_path

    _log.info("Detached processes hook completed")

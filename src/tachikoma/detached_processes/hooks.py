"""Bootstrap hook for the detached processes subsystem.

Creates the log directory, instantiates the repository, and runs
crash recovery for any records still marked as running.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.database import Database
from tachikoma.detached_processes.reconcile import reconcile_exit
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.detached_processes.spawn import is_alive

_log = logger.bind(component="detached_processes")


async def detached_processes_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize detached process repository and run crash recovery.

    Retrieves the shared Database from ctx.extras, creates the
    ProcessRepository, and reconciles any running records whose
    processes are no longer alive. Stores the repository and log
    directory path in ctx.extras.

    Keys written to ctx.extras:
        "process_repository" -> ProcessRepository instance
        "detached_process_log_dir" -> Path to the log directory
    """
    _log.info("Detached processes hook started")

    database: Database = ctx.extras["database"]
    workspace_path = ctx.settings_manager.settings.workspace.path
    log_dir = workspace_path / ".tachikoma" / "detached-processes"

    log_dir.mkdir(parents=True, exist_ok=True)

    repository = ProcessRepository(database.session_factory)

    # Crash recovery: reconcile running records whose processes are dead
    # Notifications suppressed — user shouldn't get a burst on restart
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

    _log.info("Detached processes hook completed")

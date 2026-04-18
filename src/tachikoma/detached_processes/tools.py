"""MCP tool server for detached process management.

Provides six tools: start_process, list_processes, get_process,
read_process_output, stop_process, rename_process.

Follows DES-006 factory pattern with Pydantic arg models and
extracted handler functions.
"""

import signal
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel, ValidationError

from tachikoma.detached_processes.errors import ProcessRepositoryError
from tachikoma.detached_processes.log_io import read_tail, read_window
from tachikoma.detached_processes.reconcile import reconcile_exit
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.detached_processes.spawn import is_alive, spawn_process, terminate

if TYPE_CHECKING:
    from bubus import EventBus

_log = logger.bind(component="detached_process_tools")


def _error(text: str) -> dict:
    return {"is_error": True, "content": [{"type": "text", "text": text}]}


def _msg(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _not_found(process_id: str) -> dict:
    return _error(f"Process '{process_id}' not found.")


def _repo_error(exc: ProcessRepositoryError) -> dict:
    cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
    return _error(f"{exc}{cause}")


def _unexpected(exc: Exception) -> dict:
    return _error(f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Pydantic arg models
# ---------------------------------------------------------------------------


class StartProcessArgs(BaseModel):
    name: str
    command: str
    cwd: str | None = None
    env: dict[str, str] | None = None


class ListProcessesArgs(BaseModel):
    archived: bool = False


class GetProcessArgs(BaseModel):
    process_id: str


class ReadProcessOutputArgs(BaseModel):
    process_id: str
    offset: int | None = None
    count: int | None = None


class StopProcessArgs(BaseModel):
    process_id: str
    signal: str | None = None
    timeout: float = 10.0


class RenameProcessArgs(BaseModel):
    process_id: str
    name: str


# ---------------------------------------------------------------------------
# Tool server factory (DES-006)
# ---------------------------------------------------------------------------


def create_detached_process_tools_server(
    repository: ProcessRepository,
    bus: "EventBus",
    log_dir: Path,
    timezone: ZoneInfo,
) -> McpSdkServerConfig:
    """Create an MCP server exposing detached process management tools.

    Args:
        repository: The ProcessRepository to use for persistence.
        bus: The EventBus for notification dispatch.
        log_dir: Path to the log directory for process output files.
        timezone: The configured timezone for datetime display.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "start_process",
        "Start a detached shell command that runs independently of Tachikoma.\n"
        "\n"
        "Parameters:\n"
        "- name (str, required): Display label for the process (non-unique)\n"
        "- command (str, required): Shell command to run (supports pipes, &&, etc.)\n"
        "- cwd (str, optional): Working directory (defaults to Tachikoma's cwd)\n"
        "- env (dict, optional): Environment variable overrides merged onto OS env\n"
        "\n"
        "The spawned process survives Tachikoma restarts. "
        "Returns the record ID, PID, and log path.",
        StartProcessArgs.model_json_schema(),
    )
    async def start_process(args: dict) -> dict:
        try:
            parsed = StartProcessArgs.model_validate(args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {exc}")

        try:
            cwd = Path(parsed.cwd) if parsed.cwd else None
            record = await spawn_process(
                name=parsed.name,
                command=parsed.command,
                cwd=cwd,
                env_overrides=parsed.env,
                log_dir=log_dir,
                repository=repository,
            )

            text = (
                f"Process '{record.name}' started.\n"
                f"- ID: {record.id}\n"
                f"- PID: {record.pid}\n"
                f"- Log: {record.log_path}"
            )
            return _msg(text)

        except (ValueError, OSError) as exc:
            return _error(str(exc))
        except ProcessRepositoryError as exc:
            return _repo_error(exc)
        except Exception as exc:
            _log.exception("Unexpected error starting process: {err}", err=str(exc))
            return _unexpected(exc)

    @tool(
        "list_processes",
        "List detached processes.\n"
        "\n"
        "Parameters:\n"
        "- archived (bool, optional, default false): Set true to show exited.\n"
        "\n"
        "Each entry includes ID, name, command, PID, status, and timestamps.",
        ListProcessesArgs.model_json_schema(),
    )
    async def list_processes(args: dict) -> dict:
        try:
            parsed = ListProcessesArgs.model_validate(args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {exc}")

        try:
            if parsed.archived:
                records = await repository.list_exited()
            else:
                records = await repository.list_running()

            # Lazy reconciliation for running records
            if not parsed.archived:
                for record in list(records):
                    if not is_alive(record):
                        await reconcile_exit(
                            record.id,
                            repository=repository,
                            bus=bus,
                            log_dir=log_dir,
                        )

                # Re-fetch after reconciliation
                records = await repository.list_running()

            if not records:
                label = "exited" if parsed.archived else "running"
                return _msg(f"No {label} processes found.")

            lines = ["# Detached Processes\n"]
            for r in records:
                started = r.started_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")
                lines.append(f"- [{r.id}] **{r.name}** (pid {r.pid}) {r.status}")
                lines.append(f"  Command: {r.command}")
                lines.append(f"  Started: {started}")

                if r.exited_at is not None:
                    exited = r.exited_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")
                    lines.append(f"  Exited: {exited} (code: {r.exit_code})")

                lines.append("")

            return _msg("\n".join(lines))

        except ProcessRepositoryError as exc:
            return _repo_error(exc)
        except Exception as exc:
            _log.exception("Unexpected error listing processes: {err}", err=str(exc))
            return _unexpected(exc)

    @tool(
        "get_process",
        "Get full details for a specific detached process.\n"
        "\n"
        "Parameters:\n"
        "- process_id (str, required): ID of the process to inspect",
        GetProcessArgs.model_json_schema(),
    )
    async def get_process(args: dict) -> dict:
        try:
            parsed = GetProcessArgs.model_validate(args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {exc}")

        try:
            record = await repository.get(parsed.process_id)

            if record is None:
                return _not_found(parsed.process_id)

            # Lazy reconciliation
            if record.status == "running" and not is_alive(record):
                await reconcile_exit(
                    record.id,
                    repository=repository,
                    bus=bus,
                    log_dir=log_dir,
                )
                record = await repository.get(parsed.process_id)

            if record is None:
                return _not_found(parsed.process_id)

            started = record.started_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")
            lines = [
                f"# {record.name}\n",
                f"- ID: {record.id}",
                f"- Status: {record.status}",
                f"- PID: {record.pid}",
                f"- Command: {record.command}",
                f"- CWD: {record.cwd}",
                f"- Log: {record.log_path}",
                f"- Started: {started}",
            ]

            if record.exited_at is not None:
                exited = record.exited_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")
                lines.append(f"- Exited: {exited}")
                lines.append(f"- Exit code: {record.exit_code}")

            return _msg("\n".join(lines))

        except ProcessRepositoryError as exc:
            return _repo_error(exc)
        except Exception as exc:
            _log.exception("Unexpected error getting process: {err}", err=str(exc))
            return _unexpected(exc)

    @tool(
        "read_process_output",
        "Read output from a detached process's log file.\n"
        "\n"
        "Parameters:\n"
        "- process_id (str, required): ID of the process\n"
        "- offset (int, optional): Line offset for paging (0-based)\n"
        "- count (int, optional): Number of lines to read\n"
        "\n"
        "Defaults to the last 100 lines. "
        "Use offset/count for paging through older output.",
        ReadProcessOutputArgs.model_json_schema(),
    )
    async def read_process_output(args: dict) -> dict:
        try:
            parsed = ReadProcessOutputArgs.model_validate(args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {exc}")

        try:
            record = await repository.get(parsed.process_id)

            if record is None:
                return _not_found(parsed.process_id)

            log_path = Path(record.log_path)

            if not log_path.exists() or log_path.stat().st_size == 0:
                return _msg("No output yet.")

            if parsed.offset is not None and parsed.count is not None:
                lines = read_window(log_path, parsed.offset, parsed.count)
            else:
                count = parsed.count if parsed.count is not None else 100
                lines = read_tail(log_path, count)

            if not lines:
                return _msg("No output yet.")

            return _msg("\n".join(lines))

        except FileNotFoundError:
            return _error(f"Log file not found for process '{parsed.process_id}'.")
        except ProcessRepositoryError as exc:
            return _repo_error(exc)
        except Exception as exc:
            _log.exception("Unexpected error reading process output: {err}", err=str(exc))
            return _unexpected(exc)

    @tool(
        "stop_process",
        "Stop a running detached process.\n"
        "\n"
        "Parameters:\n"
        "- process_id (str, required): ID of the process to stop\n"
        "- signal (str, optional): Signal name (default SIGTERM). "
        "Options: SIGTERM, SIGINT, SIGKILL, etc.\n"
        "- timeout (float, optional, default 10): Seconds before SIGKILL. "
        "Set 0 to send signal and return immediately.\n"
        "\n"
        "If the process has already exited, returns 'already stopped' "
        "without error.",
        StopProcessArgs.model_json_schema(),
    )
    async def stop_process(args: dict) -> dict:
        try:
            parsed = StopProcessArgs.model_validate(args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {exc}")

        try:
            record = await repository.get(parsed.process_id)

            if record is None:
                return _not_found(parsed.process_id)

            # Lazy reconciliation — no notification from stop (R15)
            if record.status == "running" and not is_alive(record):
                await reconcile_exit(
                    record.id,
                    repository=repository,
                    bus=bus,
                    log_dir=log_dir,
                    dispatch_notification=False,
                )
                record = await repository.get(parsed.process_id)
                if record is None:
                    return _not_found(parsed.process_id)
                return _msg(
                    f"Process '{record.name}' already stopped (exit code: {record.exit_code})."
                )

            if record.status == "exited":
                return _msg(
                    f"Process '{record.name}' already stopped (exit code: {record.exit_code})."
                )

            # Parse signal
            sig = signal.SIGTERM
            if parsed.signal:
                try:
                    sig = signal.Signals[parsed.signal]
                except KeyError:
                    return _error(f"Unknown signal: {parsed.signal}")

            try:
                await terminate(record, sig=sig, timeout=parsed.timeout)
            except PermissionError:
                return _error(f"Permission denied: cannot signal process {record.pid}.")

            # timeout=0 is fire-and-forget — let the watcher reconcile the actual exit
            if parsed.timeout == 0:
                return _msg(f"Signal sent to process '{record.name}'.")

            # Reconcile after termination — no notification (R15)
            await reconcile_exit(
                record.id,
                repository=repository,
                bus=bus,
                log_dir=log_dir,
                dispatch_notification=False,
            )

            updated = await repository.get(parsed.process_id)
            code = updated.exit_code if updated else "unknown"
            return _msg(f"Process '{record.name}' stopped (exit code: {code}).")

        except ProcessRepositoryError as exc:
            return _repo_error(exc)
        except Exception as exc:
            _log.exception("Unexpected error stopping process: {err}", err=str(exc))
            return _unexpected(exc)

    @tool(
        "rename_process",
        "Rename a detached process record.\n"
        "\n"
        "Parameters:\n"
        "- process_id (str, required): ID of the process to rename\n"
        "- name (str, required): New display name (must not be empty)\n"
        "\n"
        "Only updates the stored name — does not affect the running process.",
        RenameProcessArgs.model_json_schema(),
    )
    async def rename_process(args: dict) -> dict:
        try:
            parsed = RenameProcessArgs.model_validate(args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {exc}")

        if not parsed.name.strip():
            return _error("Name must not be empty or whitespace.")

        try:
            record = await repository.get(parsed.process_id)

            if record is None:
                return _not_found(parsed.process_id)

            await repository.update(parsed.process_id, name=parsed.name)

            return _msg(f"Process renamed to '{parsed.name}'.")

        except ProcessRepositoryError as exc:
            return _repo_error(exc)
        except Exception as exc:
            _log.exception("Unexpected error renaming process: {err}", err=str(exc))
            return _unexpected(exc)

    return create_sdk_mcp_server(
        name="detached-process-tools",
        version="1.0.0",
        tools=[
            start_process,
            list_processes,
            get_process,
            read_process_output,
            stop_process,
            rename_process,
        ],
    )

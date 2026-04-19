"""Database dump and restore using sqlite-diffable.

Dumps the workspace SQLite DB to diffable text files (.ndjson + .metadata.json
per table) for git tracking, and restores the DB from those dumps on sync.
"""

import asyncio
import shutil
import sys
from pathlib import Path

from loguru import logger

_log = logger.bind(component="git.db_sync")


def _sqlite_diffable_bin() -> str:
    # When tachikoma is installed via `uv tool install`, dep CLIs are not
    # symlinked to ~/.local/bin — they only exist in the tool venv's bin dir
    # alongside our python. Resolve the sibling first, fall back to PATH for
    # editable/system installs.
    candidate = Path(sys.executable).parent / "sqlite-diffable"

    return str(candidate) if candidate.exists() else "sqlite-diffable"


async def _run_sqlite_diffable(*args: str) -> None:
    """Run a sqlite-diffable CLI command.

    Args:
        *args: Command arguments (subcommand, paths, flags).

    Raises:
        RuntimeError: If sqlite-diffable exits with non-zero code.
    """
    proc = await asyncio.create_subprocess_exec(
        _sqlite_diffable_bin(),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"sqlite-diffable {' '.join(args[:2])} failed: {error_msg}")


async def dump_database(db_path: Path, dump_dir: Path) -> None:
    """Dump the workspace DB to diffable text files.

    Clears the dump directory before writing so stale files from dropped
    tables don't accumulate.

    Args:
        db_path: Path to the SQLite database file.
        dump_dir: Directory to write dump files into.
    """
    if not db_path.exists():
        _log.debug("DB file not found, skipping dump: path={path}", path=str(db_path))
        return

    shutil.rmtree(dump_dir, ignore_errors=True)
    dump_dir.mkdir(parents=True, exist_ok=True)

    await _run_sqlite_diffable("dump", str(db_path), str(dump_dir), "--all")
    _log.debug("DB dumped to diffable files: dir={dir}", dir=str(dump_dir))


async def restore_database(db_path: Path, dump_dir: Path) -> None:
    """Restore the workspace DB from diffable text files.

    Deletes the existing DB file and rebuilds it from the dump directory.

    Args:
        db_path: Path to the SQLite database file.
        dump_dir: Directory containing dump files.
    """
    db_path.unlink(missing_ok=True)
    await _run_sqlite_diffable("load", str(db_path), str(dump_dir), "--replace")
    _log.info("DB restored from dump files: path={path}", path=str(db_path))

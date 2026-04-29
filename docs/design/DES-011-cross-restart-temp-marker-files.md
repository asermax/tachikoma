# DES-011: Cross-Restart Temp Marker Files

**Scope**: Python / Architecture
**Date**: 2026-04-29

## Pattern

State that must outlive the current process — across an `os.execv` boundary or an unexpected restart — and must be readable **before** bootstrap completes (i.e. before the database is available) is persisted as a JSON file under the system temp directory. Each marker has three companion helpers in the owning subsystem:

- `write_X(...)` — serialize a frozen dataclass to JSON via `Path.write_text(json.dumps(data))`.
- `read_X() -> X | None` — return `None` on missing file (`FileNotFoundError`) and on malformed input (`json.JSONDecodeError`, missing/invalid keys), logging the malformed case at warning level.
- `clear_X()` — remove the file via `Path.unlink()` wrapped in `contextlib.suppress(FileNotFoundError)` so it is idempotent.

The path lives at module scope under the marker's owning module:

```python
import tempfile
from pathlib import Path

X_PATH = Path(tempfile.gettempdir()) / "tachikoma-<purpose>.json"
```

Consumers always pair the read with a clear-before-use to make consumption **consume-once**: clear the marker first, then perform any side effect that uses its data. A failure during the side effect must not leave the marker on disk to re-fire on the next run.

## Rationale

Some state genuinely needs to survive a process replacement and be visible to the next boot **before** any subsystem comes up:

- **`os.execv` boundary**: All Python state is replaced. Anything kept in memory is gone.
- **Pre-bootstrap timing**: ADR-013's `app_state` table requires `database_hook` to have run. Markers consumed during the early rollback path or during the initial restart-notification check must be readable earlier than that.
- **Unscheduled restart**: Crashes, signals, and rollback-driven re-execs all need transient bridging state that the caller does not have time to persist through normal channels.

Files in the system temp directory satisfy all three: they are immediately available on startup, survive the execv boundary, and disappear on host reboot (acceptable — reboot ends the cross-restart window anyway).

The malformed-tolerant `read_X` and idempotent `clear_X` shapes are non-negotiable: a corrupted marker must never crash startup, and a clear of an already-absent file must never raise — startup paths cannot afford to handle half-written transient state.

## Examples

### Do This

```python
# tachikoma/updates/rollback.py
RESTART_NOTIFICATION_PATH = Path(tempfile.gettempdir()) / "tachikoma-restart-notification.json"

@dataclass(frozen=True)
class RestartNotification:
    reason: Literal["update", "manual"]
    rollback_marker_present: bool
    previous_version: str | None
    new_version: str | None
    timestamp: str

def write_restart_notification(...) -> None:
    data = {...}
    RESTART_NOTIFICATION_PATH.write_text(json.dumps(data))

def read_restart_notification() -> RestartNotification | None:
    try:
        data = json.loads(RESTART_NOTIFICATION_PATH.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning("Malformed restart notification, ignoring: {err}", err=exc)
        return None
    try:
        return RestartNotification(**data)
    except KeyError as exc:
        _log.warning("Malformed restart notification, ignoring: {err}", err=exc)
        return None

def clear_restart_notification() -> None:
    with contextlib.suppress(FileNotFoundError):
        RESTART_NOTIFICATION_PATH.unlink()
```

Caller in `__main__.py`:

```python
notification = read_restart_notification()
marker_existed = notification is not None or RESTART_NOTIFICATION_PATH.exists()

if notification is None:
    if marker_existed:
        clear_restart_notification()  # AC5: stale/malformed → clear, no action
    return

clear_restart_notification()  # consume-once: clear FIRST
try:
    await schedule_back_online_task(notification)
except Exception:
    _log.exception("Scheduling failed; marker already cleared, no re-fire")
```

**Why**: Startup paths can read the marker before any subsystem is initialized. The clear-before-side-effect ordering means a downstream failure cannot leave the marker on disk to re-trigger.

### Don't Do This

```python
# Don't write to the workspace
MARKER_PATH = settings.workspace.path / ".tachikoma" / "restart.json"
```

**Why**: Workspace path is only available after `workspace_hook` runs. Anything that needs to survive a restart and be readable in the early rollback path can't depend on bootstrap.

```python
# Don't use environment variables
os.environ["TACHIKOMA_RESTART_REASON"] = "update"
os.execv(sys.argv[0], sys.argv)
```

**Why**: Not guaranteed to survive across all `os.execv` variants, opaque to inspection, and unstructured (a single string per slot).

```python
# Don't use the app_state table for cross-restart-only state
await app_state_repo.set("restart.reason", "update")
```

**Why**: Cross-restart bridging is consumed once and discarded. Putting it in the database means the database has to be up before you can read it (which excludes the rollback path), and the row hangs around as garbage if the consumer forgets to delete it. Use `app_state` for **long-lived** state per ADR-013.

```python
# Don't omit the malformed-clear at the call site
def read_my_marker() -> Marker | None:
    ...  # returns None on bad JSON but doesn't delete the file
```

**Why**: A malformed marker that's not cleared trips every subsequent startup. The helper symmetry across markers (rollback marker, rollback notification, restart notification) is preserved by keeping `read_X` non-mutating; the **caller** is responsible for clearing a malformed marker when it observes `read_X() is None and PATH.exists()`.

## Scope

Applies to subsystem state that must:
1. Survive an `os.execv`, signal-driven exit, or rollback-driven re-exec, AND
2. Be readable BEFORE the database (or any other subsystem) is initialized.

Current users:
- `tachikoma-update-pending.json` — pending-rollback marker written by `apply_update`, consumed at startup to drive automatic rollback on failed bootstrap.
- `tachikoma-update-rollback.json` — rollback notification written when rollback succeeds, consumed on the recovered version's startup to notify the user.
- `tachikoma-restart-notification.json` — restart notification written before any `restart`-tool execv, consumed on the next run to schedule a "back online" session task.

Does NOT apply to:
- **Long-lived state** (dedup keys, user preferences, last-seen versions) — those go through `app_state` per ADR-013.
- **In-process transient state** (current session, in-flight requests) — those live in memory.
- **Workspace artifacts** (memory files, transcripts, session metadata) — those live under the workspace path managed by their respective subsystems.

## Related

- ADR-013: Key-Value Application State Table — the long-lived counterpart; cross-restart bridging that does NOT need to be readable pre-bootstrap should live there.
- DES-003: Subsystem-Owned Bootstrap Hooks — markers are read at module scope and consumed in `__main__.py` alongside subsystem hooks.
- `docs/feature-designs/distribution/update-checker.md` — the canonical user of this pattern; the design doc cites this DES rather than re-deriving the rationale.

"""Transcript archive processor.

Copies the conversation transcript from the SDK's storage location to
the project workspace on session close. Runs in the PRE_FINALIZE phase
(after memory extraction, before git commit).
"""

import shutil
from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import PostProcessor
from tachikoma.sessions.model import Session

_log = logger.bind(component="transcript_archive")


class TranscriptArchiveProcessor(PostProcessor):
    """Post-processor that archives the SDK transcript to the workspace.

    Copies ``session.transcript_path`` to
    ``memories/transcripts/<sdk-session-id>.jsonl`` inside the workspace.
    Never raises — all errors are logged and swallowed so other processors
    and the git commit remain unaffected.
    """

    _status_message = "Archiving transcript..."

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        self._cwd = agent_defaults.cwd

    async def process(self, session: Session, *, extra: dict | None = None) -> None:
        if session.transcript_path is None or session.sdk_session_id is None:
            _log.debug(
                "Skipping transcript archive: missing fields session={sid} "
                "transcript_path={tp} sdk_session_id={ssid}",
                sid=session.id[:8],
                tp=session.transcript_path,
                ssid=session.sdk_session_id,
            )
            return

        dest_dir = self._cwd / "memories" / "transcripts"
        dest = dest_dir / f"{session.sdk_session_id}.jsonl"
        src = Path(session.transcript_path)

        # Self-healing: recreate directory if it was removed after bootstrap
        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(src, dest)
        except FileNotFoundError:
            _log.warning(
                "SDK transcript not found, skipping archive: session={sid} src={src} dest={dest}",
                sid=session.id[:8],
                src=str(src),
                dest=str(dest),
            )
            return
        except OSError:
            _log.exception(
                "Failed to archive transcript: session={sid} src={src} dest={dest}",
                sid=session.id[:8],
                src=str(src),
                dest=str(dest),
            )
            return

        _log.info(
            "Archived transcript: session={sid} dest={dest}",
            sid=session.id[:8],
            dest=str(dest),
        )

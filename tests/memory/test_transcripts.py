"""Tests for TranscriptArchiveProcessor.

Tests for DLT-099: Archive conversation transcripts to project workspace.
"""

from datetime import UTC, datetime
from pathlib import Path

from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.transcripts import TranscriptArchiveProcessor
from tachikoma.sessions.model import Session


def _make_session(
    transcript_path: str | None = None,
    sdk_session_id: str | None = "sdk-abc123",
) -> Session:
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
        transcript_path=transcript_path,
    )


class TestTranscriptArchiveProcessor:
    """Tests for TranscriptArchiveProcessor."""

    async def test_happy_path_copies_transcript(self, tmp_path: Path) -> None:
        """AC (R0, R1, R6, R8): Source file copied to
        memories/transcripts/<sdk-session-id>.jsonl."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        sdk_transcript = tmp_path / "sdk" / "sdk-abc123.jsonl"
        sdk_transcript.parent.mkdir()
        sdk_transcript.write_bytes(b'{"turn": 1}\n{"turn": 2}\n')

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(
            transcript_path=str(sdk_transcript),
            sdk_session_id="sdk-abc123",
        )
        await processor.process(session)

        archived = workspace / "memories" / "transcripts" / "sdk-abc123.jsonl"
        assert archived.exists()
        assert archived.read_bytes() == sdk_transcript.read_bytes()

    async def test_null_transcript_path_skips(self, tmp_path: Path) -> None:
        """AC (R3): Null transcript_path — processor skips, no file created."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(transcript_path=None, sdk_session_id="sdk-abc123")
        await processor.process(session)

        assert not (workspace / "memories" / "transcripts" / "sdk-abc123.jsonl").exists()

    async def test_null_sdk_session_id_skips(self, tmp_path: Path) -> None:
        """AC (R3): Null sdk_session_id — processor skips, no file created."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        sdk_transcript = tmp_path / "sdk" / "some.jsonl"
        sdk_transcript.parent.mkdir()
        sdk_transcript.write_text("data")

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(
            transcript_path=str(sdk_transcript),
            sdk_session_id=None,
        )
        await processor.process(session)

        transcripts_dir = workspace / "memories" / "transcripts"
        if transcripts_dir.exists():
            assert list(transcripts_dir.iterdir()) == []

    async def test_missing_source_file_skips(self, tmp_path: Path) -> None:
        """AC (R3): Source file missing — processor returns normally, no file created."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(
            transcript_path=str(tmp_path / "nonexistent.jsonl"),
            sdk_session_id="sdk-abc123",
        )
        await processor.process(session)

        archived = workspace / "memories" / "transcripts" / "sdk-abc123.jsonl"
        assert not archived.exists()

    async def test_filesystem_error_on_copy_swallowed(
        self,
        tmp_path: Path,
        mocker: MockerFixture,
    ) -> None:
        """AC (R3): PermissionError on copy — processor returns without raising."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        sdk_transcript = tmp_path / "sdk" / "sdk-abc123.jsonl"
        sdk_transcript.parent.mkdir()
        sdk_transcript.write_text("data")

        mocker.patch(
            "tachikoma.memory.transcripts.shutil.copy2",
            side_effect=PermissionError("denied"),
        )

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(
            transcript_path=str(sdk_transcript),
            sdk_session_id="sdk-abc123",
        )
        await processor.process(session)  # Should not raise

    async def test_idempotent_overwrites_existing(self, tmp_path: Path) -> None:
        """AC (R4): Running twice overwrites with latest source."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        sdk_transcript = tmp_path / "sdk" / "sdk-abc123.jsonl"
        sdk_transcript.parent.mkdir()
        sdk_transcript.write_text("version-1")

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(
            transcript_path=str(sdk_transcript),
            sdk_session_id="sdk-abc123",
        )

        await processor.process(session)
        sdk_transcript.write_text("version-2")
        await processor.process(session)

        archived = workspace / "memories" / "transcripts" / "sdk-abc123.jsonl"
        assert archived.read_text() == "version-2"

    async def test_self_healing_mkdir(self, tmp_path: Path) -> None:
        """AC (D6): Copy succeeds even when memories/transcripts/ was deleted after bootstrap."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        sdk_transcript = tmp_path / "sdk" / "sdk-abc123.jsonl"
        sdk_transcript.parent.mkdir()
        sdk_transcript.write_text("data")

        processor = TranscriptArchiveProcessor(AgentDefaults(cwd=workspace))
        session = _make_session(
            transcript_path=str(sdk_transcript),
            sdk_session_id="sdk-abc123",
        )

        # Create and then remove the transcripts directory
        transcripts_dir = workspace / "memories" / "transcripts"
        transcripts_dir.mkdir(parents=True)
        transcripts_dir.rmdir()
        transcripts_dir.parent.rmdir()

        await processor.process(session)

        archived = workspace / "memories" / "transcripts" / "sdk-abc123.jsonl"
        assert archived.exists()
        assert archived.read_text() == "data"

"""Initial baseline schema for sessions table.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-21

Captures the pre-Alembic schema as the baseline for existing databases.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial sessions table if it doesn't exist."""
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("sdk_session_id", sa.String(), nullable=True),
        sa.Column("transcript_path", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Index("ix_sessions_started_at", "started_at"),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Drop the sessions table."""
    op.drop_index("ix_sessions_started_at", table_name="sessions", if_exists=True)
    op.drop_table("sessions", if_exists=True)

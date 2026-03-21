"""Add last_resumed_at to sessions and create session_resumptions table.

Revision ID: 002_dlt028_resumption
Revises: 001_initial
Create Date: 2026-03-21

DLT-028: Resume conversation on topic revisit.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "002_dlt028_resumption"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add last_resumed_at column and session_resumptions table."""
    # Add last_resumed_at to sessions table
    op.add_column(
        "sessions",
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )

    # Create session_resumptions table
    op.create_table(
        "session_resumptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "resumed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "previous_ended_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        if_not_exists=True,
    )

    # Create index on session_id for faster lookups
    op.create_index(
        "ix_session_resumptions_session_id",
        "session_resumptions",
        ["session_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove session_resumptions table and last_resumed_at column."""
    op.drop_index(
        "ix_session_resumptions_session_id",
        table_name="session_resumptions",
        if_exists=True,
    )
    op.drop_table("session_resumptions", if_exists=True)
    op.drop_column("sessions", "last_resumed_at", if_exists=True)

"""Initial schema for task definitions and instances tables.

Revision ID: 001_dlt010_initial
Revises:
Create Date: 2026-03-21
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "001_dlt010_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the task_definitions and task_instances tables."""
    # Create task_definitions table
    op.create_table(
        "task_definitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("schedule", sa.String(), nullable=False),  # JSON string
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("prompt", sa.String(), nullable=False),
        sa.Column("notify", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )

    # Create task_instances table
    op.create_table(
        "task_instances",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "definition_id",
            sa.String(),
            sa.ForeignKey("task_definitions.id"),
            nullable=True,
        ),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("prompt", sa.String(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )

    # Create indexes for efficient queries
    op.create_index(
        "ix_task_instances_status",
        "task_instances",
        ["status"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_task_instances_task_type",
        "task_instances",
        ["task_type"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Drop the task tables and indexes."""
    op.drop_index("ix_task_instances_task_type", table_name="task_instances", if_exists=True)
    op.drop_index("ix_task_instances_status", table_name="task_instances", if_exists=True)
    op.drop_table("task_instances", if_exists=True)
    op.drop_table("task_definitions", if_exists=True)

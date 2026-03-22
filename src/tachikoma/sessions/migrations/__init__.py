"""Alembic migration environment for session schema.

This package contains:
- migrations_path: Path to the migrations directory
- run_migrations: Function to run migrations via a sync connection
"""

from pathlib import Path

migrations_path = Path(__file__).parent

"""Establish an intentionally empty migration baseline.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish the migration infrastructure without domain tables."""


def downgrade() -> None:
    """Return to the pre-migration baseline."""

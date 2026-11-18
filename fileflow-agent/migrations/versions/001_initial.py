"""Initial schema: the runs ledger table.

Revision ID: 001
Revises: (none)
Create Date: 2026-11-17

Creates the ``runs`` table exactly as the SQLAlchemy ``Run`` model in
infrastructure/persistence.py defines it, so a database built by ``alembic upgrade head``
and one built by the ledger's ``create_all`` are identical -- and an
existing database (built either way) can be marked current with
``alembic stamp head`` instead of being rebuilt.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("job", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("input_sha256", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("runs")

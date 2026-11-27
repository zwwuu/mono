"""Drop the input_sha256 column from the runs ledger.

The manifest no longer carries a manifest-level input hash -- each
file's hash lives in the manifest's files[], and a run is identified
by its run_id alone (idempotency key job|run_id). The column held
the old combined fingerprint, which named no file and decided no
duplicate. Revision 001 created it; this revision removes it.
"""

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.drop_column("runs", "input_sha256")


def downgrade() -> None:
    op.add_column("runs", sa.Column("input_sha256", sa.String(), nullable=True))

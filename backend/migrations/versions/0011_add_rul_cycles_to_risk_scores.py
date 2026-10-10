"""add nullable rul_cycles to risk_scores (Phase 3b, task 3b.7a)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10

Stores the Remaining Useful Life estimate (in cycles) next to the risk score it was
computed with. Nullable: assets without C-MAPSS-style telemetry, or runs where no RUL model
is published, simply have no RUL. The table keeps its existing RLS policy and grants -
adding a column changes neither - so no new policy is created here; the existing
cross-tenant tests (tests/cross_tenant/test_risk_scores_rls.py) still cover this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("risk_scores", sa.Column("rul_cycles", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_risk_scores_rul_cycles_nonnegative",
        "risk_scores",
        "rul_cycles IS NULL OR rul_cycles >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_risk_scores_rul_cycles_nonnegative", "risk_scores", type_="check")
    op.drop_column("risk_scores", "rul_cycles")

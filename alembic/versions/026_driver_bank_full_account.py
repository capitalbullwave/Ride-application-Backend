"""Store full driver bank account number for admin visibility."""
revision = "026_driver_bank_full_account"
down_revision = "025_user_withdrawals"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if _has_table("driver_bank_accounts") and not _has_column("driver_bank_accounts", "account_number"):
        op.add_column(
            "driver_bank_accounts",
            sa.Column("account_number", sa.String(length=30), nullable=True),
        )


def downgrade() -> None:
    if _has_table("driver_bank_accounts") and _has_column("driver_bank_accounts", "account_number"):
        op.drop_column("driver_bank_accounts", "account_number")

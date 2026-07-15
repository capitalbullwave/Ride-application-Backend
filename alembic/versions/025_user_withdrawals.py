"""Add user bank accounts and support user wallet withdrawals."""
revision = "025_user_withdrawals"
down_revision = "024_referral_earn"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_table("user_bank_accounts"):
        op.create_table(
            "user_bank_accounts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_holder_name", sa.String(length=150), nullable=False),
            sa.Column("account_number_masked", sa.String(length=50), nullable=False),
            sa.Column("ifsc_code", sa.String(length=20), nullable=False),
            sa.Column("bank_name", sa.String(length=100), nullable=False),
            sa.Column("upi_id", sa.String(length=100), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
        op.create_index("ix_user_bank_accounts_user_id", "user_bank_accounts", ["user_id"])
        op.create_index("ix_user_bank_primary", "user_bank_accounts", ["user_id", "is_primary"])

    if _has_table("withdrawal_requests"):
        if not _has_column("withdrawal_requests", "user_id"):
            op.add_column(
                "withdrawal_requests",
                sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            )
            op.create_index("ix_withdrawal_requests_user_id", "withdrawal_requests", ["user_id"])
            op.create_index("ix_withdrawals_user_status", "withdrawal_requests", ["user_id", "status"])

        if not _has_column("withdrawal_requests", "user_bank_account_id"):
            op.add_column(
                "withdrawal_requests",
                sa.Column(
                    "user_bank_account_id",
                    postgresql.UUID(as_uuid=True),
                    sa.ForeignKey("user_bank_accounts.id", ondelete="RESTRICT"),
                    nullable=True,
                ),
            )

        # Allow driver-only rows to remain; make driver fields nullable for user withdrawals
        op.alter_column("withdrawal_requests", "driver_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
        op.alter_column(
            "withdrawal_requests",
            "bank_account_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )


def downgrade() -> None:
    if _has_table("withdrawal_requests"):
        if _has_column("withdrawal_requests", "user_bank_account_id"):
            op.drop_column("withdrawal_requests", "user_bank_account_id")
        if _has_column("withdrawal_requests", "user_id"):
            op.drop_index("ix_withdrawals_user_status", table_name="withdrawal_requests")
            op.drop_index("ix_withdrawal_requests_user_id", table_name="withdrawal_requests")
            op.drop_column("withdrawal_requests", "user_id")
    if _has_table("user_bank_accounts"):
        op.drop_index("ix_user_bank_primary", table_name="user_bank_accounts")
        op.drop_index("ix_user_bank_accounts_user_id", table_name="user_bank_accounts")
        op.drop_table("user_bank_accounts")

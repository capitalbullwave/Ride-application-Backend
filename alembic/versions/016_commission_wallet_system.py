"""Dynamic commission and driver wallet system."""
revision = "016_commission_wallet_system"
down_revision = "015_wallet_topup_payments"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    ride_cols = {c["name"] for c in inspect(bind).get_columns("rides")}

    if "commission_settings" not in tables:
        op.create_table(
            "commission_settings",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("driver_commission_percentage", sa.Float(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_commission_settings_active", "commission_settings", ["is_active"])

    if "driver_wallet" not in tables:
        op.create_table(
            "driver_wallet",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("driver_id", UUID(as_uuid=True), sa.ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("available_balance", sa.Float(), nullable=False, server_default="0"),
            sa.Column("pending_balance", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lifetime_earnings", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("driver_id", name="uq_driver_wallet_driver"),
        )
        op.create_index("ix_driver_wallet_driver_id", "driver_wallet", ["driver_id"])

    if "driver_wallet_transactions" not in tables:
        op.create_table(
            "driver_wallet_transactions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("driver_id", UUID(as_uuid=True), sa.ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ride_id", UUID(as_uuid=True), sa.ForeignKey("rides.id", ondelete="SET NULL"), nullable=True),
            sa.Column("type", sa.String(length=20), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=False),
            sa.Column("balance_after_transaction", sa.Float(), nullable=False),
            sa.Column("wallet_id", UUID(as_uuid=True), sa.ForeignKey("driver_wallet.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("ride_id", name="uq_driver_wallet_tx_ride_credit"),
        )
        op.create_index("ix_driver_wallet_tx_driver_created", "driver_wallet_transactions", ["driver_id", "created_at"])
        op.create_index("ix_driver_wallet_transactions_driver_id", "driver_wallet_transactions", ["driver_id"])
        op.create_index("ix_driver_wallet_transactions_wallet_id", "driver_wallet_transactions", ["wallet_id"])

    if "company_revenue_ledger" not in tables:
        op.create_table(
            "company_revenue_ledger",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("ride_id", UUID(as_uuid=True), sa.ForeignKey("rides.id", ondelete="CASCADE"), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("ride_id", name="uq_company_revenue_ride"),
        )
        op.create_index("ix_company_revenue_ledger_ride_id", "company_revenue_ledger", ["ride_id"])

    if "driver_commission_percentage" not in ride_cols:
        op.add_column("rides", sa.Column("driver_commission_percentage", sa.Float(), nullable=True))
    if "driver_earning" not in ride_cols:
        op.add_column("rides", sa.Column("driver_earning", sa.Float(), nullable=True))
    if "company_earning" not in ride_cols:
        op.add_column("rides", sa.Column("company_earning", sa.Float(), nullable=True))

    settings_count = bind.execute(sa.text("SELECT COUNT(*) FROM commission_settings")).scalar()
    if not settings_count:
        op.execute(
            """
            INSERT INTO commission_settings (id, driver_commission_percentage, is_active, created_at, updated_at)
            VALUES (gen_random_uuid(), 30.0, true, now(), now())
            """
        )


def downgrade() -> None:
    op.drop_column("rides", "company_earning")
    op.drop_column("rides", "driver_earning")
    op.drop_column("rides", "driver_commission_percentage")
    op.drop_table("company_revenue_ledger")
    op.drop_table("driver_wallet_transactions")
    op.drop_table("driver_wallet")
    op.drop_table("commission_settings")

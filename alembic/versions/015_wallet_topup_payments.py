"""Wallet top-up payment records."""
revision = "015_wallet_topup_payments"
down_revision = "014_subscription_payments"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def upgrade() -> None:
    op.create_table(
        "wallet_topup_payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("razorpay_order_id", sa.String(length=100), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("gateway_response", JSONB(), nullable=True),
        sa.Column(
            "wallet_transaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_wallet_topup_payments_user_id", "wallet_topup_payments", ["user_id"])
    op.create_index(
        "ix_wallet_topup_payments_razorpay_order_id",
        "wallet_topup_payments",
        ["razorpay_order_id"],
    )
    op.create_index("ix_wallet_topup_payments_status", "wallet_topup_payments", ["status"])


def downgrade() -> None:
    op.drop_table("wallet_topup_payments")

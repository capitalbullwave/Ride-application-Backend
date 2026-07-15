"""Add referral programs, rewards, and driver invite codes."""
revision = "024_referral_earn"
down_revision = "023_remove_xl"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.create_table(
        "referral_programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("required_rides", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("reward_amount", sa.Float(), nullable=False, server_default="100"),
        sa.Column("title", sa.String(length=120), nullable=False, server_default="Refer & Earn"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("share_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("audience", name="uq_referral_programs_audience"),
    )
    op.create_index("ix_referral_programs_audience", "referral_programs", ["audience"])

    op.create_table(
        "referral_rewards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("referral_programs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("referrer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("required_rides", sa.Integer(), nullable=False),
        sa.Column("reward_amount", sa.Float(), nullable=False),
        sa.Column("rides_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("audience", "referee_id", name="uq_referral_rewards_audience_referee"),
    )
    op.create_index("ix_referral_rewards_audience", "referral_rewards", ["audience"])
    op.create_index("ix_referral_rewards_referrer_id", "referral_rewards", ["referrer_id"])
    op.create_index("ix_referral_rewards_referee_id", "referral_rewards", ["referee_id"])
    op.create_index("ix_referral_rewards_status", "referral_rewards", ["status"])

    op.add_column("drivers", sa.Column("invite_code", sa.String(length=20), nullable=True))
    op.create_index("ix_drivers_invite_code", "drivers", ["invite_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_drivers_invite_code", table_name="drivers")
    op.drop_column("drivers", "invite_code")
    op.drop_table("referral_rewards")
    op.drop_table("referral_programs")

"""Per-vehicle driver commission percentage."""
revision = "017_vehicle_commission"
down_revision = "016_commission_wallet_system"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "vehicle_types",
        sa.Column("driver_commission_percentage", sa.Float(), nullable=True),
    )
    op.execute(
        """
        UPDATE vehicle_types
        SET driver_commission_percentage = COALESCE(
            (
                SELECT driver_commission_percentage
                FROM commission_settings
                WHERE is_active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            ),
            30.0
        )
        """
    )


def downgrade() -> None:
    op.drop_column("vehicle_types", "driver_commission_percentage")

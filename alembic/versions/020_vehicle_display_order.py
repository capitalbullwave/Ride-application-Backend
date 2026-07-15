"""Add display_order to vehicle_types for admin-controlled listing order."""
revision = "020_vehicle_display_order"
down_revision = "019_user_gender"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("vehicle_types")}
    if "display_order" not in cols:
        op.add_column(
            "vehicle_types",
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        )

    bind.execute(
        text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY service_group ORDER BY name
                       ) - 1 AS rn
                FROM vehicle_types
            )
            UPDATE vehicle_types
            SET display_order = ranked.rn
            FROM ranked
            WHERE vehicle_types.id = ranked.id
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("vehicle_types")}
    if "display_order" in cols:
        op.drop_column("vehicle_types", "display_order")

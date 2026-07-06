"""Add included_hours and per_hour_rate for rental vehicle pricing."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009_vehicle_rental_hours"
down_revision: Union[str, None] = "008_vehicle_service_group"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    if table not in _inspector().get_table_names():
        return False
    return column in {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    if not _has_column("vehicle_types", "included_hours"):
        op.add_column(
            "vehicle_types",
            sa.Column("included_hours", sa.Float(), nullable=False, server_default="4"),
        )
    if not _has_column("vehicle_types", "per_hour_rate"):
        op.add_column(
            "vehicle_types",
            sa.Column("per_hour_rate", sa.Float(), nullable=False, server_default="0"),
        )
    op.execute(
        "UPDATE vehicle_types SET included_hours = 4, per_hour_rate = 50 "
        "WHERE service_group = 'rental' AND per_hour_rate = 0"
    )


def downgrade() -> None:
    if _has_column("vehicle_types", "per_hour_rate"):
        op.drop_column("vehicle_types", "per_hour_rate")
    if _has_column("vehicle_types", "included_hours"):
        op.drop_column("vehicle_types", "included_hours")

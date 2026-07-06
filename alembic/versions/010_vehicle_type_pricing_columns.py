"""Add WaveGo vehicle type pricing columns missing from older schemas."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_vehicle_type_pricing_columns"
down_revision: Union[str, None] = "007_vehicle_cancellation_charge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    columns = [
        ("per_minute_rate", sa.Float(), "0"),
        ("waiting_charge_per_min", sa.Float(), "0"),
        ("included_distance_km", sa.Float(), "2"),
        ("minimum_fare", sa.Float(), "0"),
    ]
    for name, col_type, default in columns:
        if not _has_column("vehicle_types", name):
            op.add_column(
                "vehicle_types",
                sa.Column(name, col_type, nullable=False, server_default=default),
            )


def downgrade() -> None:
    for name in (
        "minimum_fare",
        "included_distance_km",
        "waiting_charge_per_min",
        "per_minute_rate",
    ):
        if _has_column("vehicle_types", name):
            op.drop_column("vehicle_types", name)

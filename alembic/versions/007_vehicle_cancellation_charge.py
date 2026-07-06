"""Add cancellation_charge to vehicle_types."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007_vehicle_cancellation_charge"
down_revision: Union[str, None] = "009_vehicle_rental_hours"
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
    if not _has_column("vehicle_types", "cancellation_charge"):
        op.add_column(
            "vehicle_types",
            sa.Column("cancellation_charge", sa.Float(), nullable=False, server_default="20"),
        )
        op.execute(
            "UPDATE vehicle_types SET cancellation_charge = GREATEST(base_fare, 20) "
            "WHERE cancellation_charge IS NULL OR cancellation_charge = 20"
        )


def downgrade() -> None:
    if _has_column("vehicle_types", "cancellation_charge"):
        op.drop_column("vehicle_types", "cancellation_charge")

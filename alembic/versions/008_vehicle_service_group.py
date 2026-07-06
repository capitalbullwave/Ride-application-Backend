"""Add service_group to vehicle_types for ride vs rental."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_vehicle_service_group"
down_revision: Union[str, None] = "005_admin_last_login_tz"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    if table not in _inspector().get_table_names():
        return False
    return column in {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    if not _has_column("vehicle_types", "service_group"):
        op.add_column(
            "vehicle_types",
            sa.Column("service_group", sa.String(length=20), nullable=False, server_default="ride"),
        )
        op.execute(
            "UPDATE vehicle_types SET service_group = 'rental' "
            "WHERE slug LIKE 'rental-%' OR slug LIKE '%-rental' OR name ILIKE '%rental%'"
        )


def downgrade() -> None:
    if _has_column("vehicle_types", "service_group"):
        op.drop_column("vehicle_types", "service_group")

"""Add optional multi-stop waypoints on rides."""
revision = "029_ride_stops"
down_revision = "028_ride_women_safety"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_table("rides"):
        return
    if not _has_column("rides", "stops"):
        op.add_column(
            "rides",
            sa.Column("stops", JSONB(), nullable=True),
        )


def downgrade() -> None:
    if not _has_table("rides"):
        return
    if _has_column("rides", "stops"):
        op.drop_column("rides", "stops")

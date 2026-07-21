"""Women Safety Ride fields on rides."""
revision = "028_ride_women_safety"
down_revision = "027_ride_women_rider_preference"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


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
    if not _has_column("rides", "women_safety_enabled"):
        op.add_column(
            "rides",
            sa.Column(
                "women_safety_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    if not _has_column("rides", "is_emergency"):
        op.add_column(
            "rides",
            sa.Column(
                "is_emergency",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    if not _has_table("rides"):
        return
    if _has_column("rides", "is_emergency"):
        op.drop_column("rides", "is_emergency")
    if _has_column("rides", "women_safety_enabled"):
        op.drop_column("rides", "women_safety_enabled")

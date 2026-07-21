"""Ride women-captain preference fields for female passengers."""
revision = "027_ride_women_rider_preference"
down_revision = "026_driver_bank_full_account"
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
    if not _has_column("rides", "prefer_women_riders"):
        op.add_column(
            "rides",
            sa.Column(
                "prefer_women_riders",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    if not _has_column("rides", "allow_all_riders"):
        op.add_column(
            "rides",
            sa.Column(
                "allow_all_riders",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade() -> None:
    if not _has_table("rides"):
        return
    if _has_column("rides", "allow_all_riders"):
        op.drop_column("rides", "allow_all_riders")
    if _has_column("rides", "prefer_women_riders"):
        op.drop_column("rides", "prefer_women_riders")

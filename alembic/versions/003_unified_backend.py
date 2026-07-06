"""Unified backend migration — token_version + ride status alignment."""
from alembic import op
import sqlalchemy as sa


revision = "003_unified_backend"
down_revision = "002_module2_database"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "token_version"):
        op.add_column(
            "users",
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.alter_column("users", "token_version", server_default=None)

    if not _has_column("drivers", "token_version"):
        op.add_column(
            "drivers",
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.alter_column("drivers", "token_version", server_default=None)

    op.execute("UPDATE rides SET status = 'SEARCHING_DRIVER' WHERE status = 'SEARCHING'")
    op.execute("UPDATE rides SET status = 'DRIVER_ASSIGNED' WHERE status = 'ACCEPTED'")
    op.execute("UPDATE ride_events SET event_type = 'SEARCHING_DRIVER' WHERE event_type = 'SEARCHING'")
    op.execute("UPDATE ride_events SET event_type = 'DRIVER_ASSIGNED' WHERE event_type = 'ACCEPTED'")


def downgrade() -> None:
    op.execute("UPDATE rides SET status = 'SEARCHING' WHERE status = 'SEARCHING_DRIVER'")
    op.execute("UPDATE rides SET status = 'ACCEPTED' WHERE status = 'DRIVER_ASSIGNED'")
    op.execute("UPDATE ride_events SET event_type = 'SEARCHING' WHERE event_type = 'SEARCHING_DRIVER'")
    op.execute("UPDATE ride_events SET event_type = 'ACCEPTED' WHERE event_type = 'DRIVER_ASSIGNED'")

    if _has_column("drivers", "token_version"):
        op.drop_column("drivers", "token_version")
    if _has_column("users", "token_version"):
        op.drop_column("users", "token_version")

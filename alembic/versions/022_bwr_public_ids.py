"""Add BWR public IDs for users, drivers, and rides."""
revision = "022_bwr_public_ids"
down_revision = "021_vehicle_sg_unique"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def _backfill_public_ids(table: str, prefix: str, sequence: str) -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            f"""
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn
                FROM {table}
            )
            UPDATE {table} AS entity
            SET public_id = '{prefix}-' || LPAD(ranked.rn::text, 6, '0')
            FROM ranked
            WHERE entity.id = ranked.id
              AND entity.public_id IS NULL
            """
        )
    )
    bind.execute(
        text(
            f"""
            SELECT setval(
                '{sequence}',
                GREATEST(COALESCE((SELECT MAX(CAST(SPLIT_PART(public_id, '-', 3) AS INTEGER)) FROM {table}), 0), 1)
            )
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text(f"CREATE SEQUENCE IF NOT EXISTS user_public_id_seq START 1"))
    bind.execute(text(f"CREATE SEQUENCE IF NOT EXISTS driver_public_id_seq START 1"))
    bind.execute(text(f"CREATE SEQUENCE IF NOT EXISTS ride_public_id_seq START 1"))

    if not _has_column("users", "public_id"):
        op.add_column("users", sa.Column("public_id", sa.String(length=20), nullable=True))
    if not _has_column("drivers", "public_id"):
        op.add_column("drivers", sa.Column("public_id", sa.String(length=20), nullable=True))
    if not _has_column("rides", "public_id"):
        op.add_column("rides", sa.Column("public_id", sa.String(length=20), nullable=True))

    _backfill_public_ids("users", "BWR-U", "user_public_id_seq")
    _backfill_public_ids("drivers", "BWR-D", "driver_public_id_seq")
    _backfill_public_ids("rides", "BWR-R", "ride_public_id_seq")

    op.alter_column("users", "public_id", nullable=False)
    op.alter_column("drivers", "public_id", nullable=False)
    op.alter_column("rides", "public_id", nullable=False)

    op.create_index("ix_users_public_id", "users", ["public_id"], unique=True)
    op.create_index("ix_drivers_public_id", "drivers", ["public_id"], unique=True)
    op.create_index("ix_rides_public_id", "rides", ["public_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {idx["name"] for idx in inspect(bind).get_indexes("users")}
    if "ix_users_public_id" in indexes:
        op.drop_index("ix_users_public_id", table_name="users")
    indexes = {idx["name"] for idx in inspect(bind).get_indexes("drivers")}
    if "ix_drivers_public_id" in indexes:
        op.drop_index("ix_drivers_public_id", table_name="drivers")
    indexes = {idx["name"] for idx in inspect(bind).get_indexes("rides")}
    if "ix_rides_public_id" in indexes:
        op.drop_index("ix_rides_public_id", table_name="rides")

    if _has_column("users", "public_id"):
        op.drop_column("users", "public_id")
    if _has_column("drivers", "public_id"):
        op.drop_column("drivers", "public_id")
    if _has_column("rides", "public_id"):
        op.drop_column("rides", "public_id")

    bind.execute(text("DROP SEQUENCE IF EXISTS user_public_id_seq"))
    bind.execute(text("DROP SEQUENCE IF EXISTS driver_public_id_seq"))
    bind.execute(text("DROP SEQUENCE IF EXISTS ride_public_id_seq"))

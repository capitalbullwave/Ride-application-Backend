"""Allow user and driver ratings per ride; add user rating_avg.

Revision ID: 012_dual_ratings
Revises: 011_driver_emergency_contacts
"""
from alembic import op
import sqlalchemy as sa

revision = "012_dual_ratings"
down_revision = "011_driver_emergency_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    rating_indexes = {idx["name"] for idx in inspector.get_indexes("ratings")}
    rating_uniques = {uc["name"] for uc in inspector.get_unique_constraints("ratings")}

    if "ratings_ride_id_key" in rating_uniques:
        op.drop_constraint("ratings_ride_id_key", "ratings", type_="unique")
    elif "uq_ratings_ride_id" in rating_uniques:
        op.drop_constraint("uq_ratings_ride_id", "ratings", type_="unique")

    if "uq_ratings_ride_rater" not in rating_uniques:
        op.create_unique_constraint("uq_ratings_ride_rater", "ratings", ["ride_id", "rater_type"])

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "rating_avg" not in user_cols:
        op.add_column(
            "users",
            sa.Column("rating_avg", sa.Float(), server_default="0", nullable=False),
        )


def downgrade() -> None:
    op.drop_constraint("uq_ratings_ride_rater", "ratings", type_="unique")
    op.create_unique_constraint("ratings_ride_id_key", "ratings", ["ride_id"])
    op.drop_column("users", "rating_avg")

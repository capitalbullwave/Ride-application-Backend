"""Allow same vehicle name/slug in different service groups (ride vs rental)."""
revision = "021_vehicle_sg_unique"
down_revision = "020_vehicle_display_order"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


def _drop_single_column_unique(bind, table: str, column: str) -> None:
    insp = inspect(bind)

    for constraint in insp.get_unique_constraints(table):
        if constraint["column_names"] == [column]:
            op.drop_constraint(constraint["name"], table, type_="unique")

    for index in insp.get_indexes(table):
        if index.get("unique") and index["column_names"] == [column]:
            op.drop_index(index["name"], table_name=table)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {uc["name"] for uc in insp.get_unique_constraints("vehicle_types")}

    _drop_single_column_unique(bind, "vehicle_types", "name")
    _drop_single_column_unique(bind, "vehicle_types", "slug")

    if "uq_vehicle_types_service_group_name" not in existing:
        op.create_unique_constraint(
            "uq_vehicle_types_service_group_name",
            "vehicle_types",
            ["service_group", "name"],
        )
    if "uq_vehicle_types_service_group_slug" not in existing:
        op.create_unique_constraint(
            "uq_vehicle_types_service_group_slug",
            "vehicle_types",
            ["service_group", "slug"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {uc["name"] for uc in insp.get_unique_constraints("vehicle_types")}

    if "uq_vehicle_types_service_group_slug" in existing:
        op.drop_constraint("uq_vehicle_types_service_group_slug", "vehicle_types", type_="unique")
    if "uq_vehicle_types_service_group_name" in existing:
        op.drop_constraint("uq_vehicle_types_service_group_name", "vehicle_types", type_="unique")

    slug_indexes = {
        idx["name"]
        for idx in insp.get_indexes("vehicle_types")
        if idx.get("unique") and idx["column_names"] == ["slug"]
    }
    if not slug_indexes:
        op.create_index("ix_vehicle_types_slug", "vehicle_types", ["slug"], unique=True)

    name_uniques = {
        uc["name"]
        for uc in insp.get_unique_constraints("vehicle_types")
        if uc["column_names"] == ["name"]
    }
    if not name_uniques:
        op.create_unique_constraint("vehicle_types_name_key", "vehicle_types", ["name"])

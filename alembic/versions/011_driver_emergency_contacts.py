"""Driver emergency contacts table."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "011_driver_emergency_contacts"
down_revision = "010_vehicle_type_pricing_columns"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("driver_emergency_contacts"):
        return

    op.create_table(
        "driver_emergency_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("relation", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_driver_emergency_contacts_driver",
        "driver_emergency_contacts",
        ["driver_id"],
    )


def downgrade() -> None:
    if _has_table("driver_emergency_contacts"):
        op.drop_index("ix_driver_emergency_contacts_driver", table_name="driver_emergency_contacts")
        op.drop_table("driver_emergency_contacts")
